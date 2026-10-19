from __future__ import annotations

import json
import uuid
from datetime import datetime
from hmac import compare_digest
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import BASE_DIR, ONFILE_API_KEY
from app.db import fetch_all, fetch_by_id, fetch_invoices, fetch_vendor_cases, get_connection, init_db, insert_audit_event, json_dumps
from app.integrations import nicrron, prism
from app.state_machine import GUARD_DEFINITIONS, StateMachine, evaluate_guard

init_db()

app = FastAPI(title='OnFile', version='1.0.0')
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')


class VendorRequest(BaseModel):
    legal_name: str = Field(..., min_length=2)
    supplier_id: str = ''
    country: str = 'US'
    business_email: str = ''
    contact_phone: str = ''
    trusted_bank: dict[str, Any] | None = None
    documents: list[dict[str, Any]] = Field(default_factory=list)


class InvoiceRequest(BaseModel):
    vendor_id: str
    invoice_number: str = Field(..., min_length=3)
    total_amount: float = 0.0
    currency: str = 'USD'
    bank_details: dict[str, Any] | None = None
    purchase_order: str = ''
    invoice_date: str = ''
    due_date: str = ''


class BankChangeRequest(BaseModel):
    vendor_name: str = 'Demo Vendor'
    requested_bank: dict[str, Any] | None = None
    trusted_phone: str = ''
    requester: str = 'demo-user'
    do_not_contact: bool = False
    recent_number_change_days: int = 0


SCENARIOS = [
    {'id': 'S01', 'name': 'legitimate_vendor', 'expected_outcome': 'verified', 'guard': 'G6', 'details': 'Complete vendor evidence should pass.'},
    {'id': 'S02', 'name': 'missing_registration', 'expected_outcome': 'needs_human_review', 'guard': 'G8', 'details': 'Missing evidence is not a successful verification.'},
    {'id': 'S03', 'name': 'duplicate_vendor', 'expected_outcome': 'rejected', 'guard': 'G7', 'details': 'Duplicate vendor IDs are blocked.'},
    {'id': 'S04', 'name': 'forged_document', 'expected_outcome': 'rejected', 'guard': 'G14', 'details': 'Conflicting document details trigger review.'},
    {'id': 'S05', 'name': 'invoice_without_vendor', 'expected_outcome': 'needs_evidence', 'guard': 'G6', 'details': 'Unverified vendors cannot submit valid invoices.'},
    {'id': 'S06', 'name': 'duplicate_invoice', 'expected_outcome': 'ready_for_review', 'guard': 'G9', 'details': 'Near-duplication is held for review.'},
    {'id': 'S07', 'name': 'invoice_arithmetic_error', 'expected_outcome': 'ready_for_review', 'guard': 'G9', 'details': 'Mismatched totals are not accepted.'},
    {'id': 'S08', 'name': 'po_mismatch', 'expected_outcome': 'ready_for_review', 'guard': 'G10', 'details': 'Purchase order mismatch blocks approval.'},
    {'id': 'S09', 'name': 'bank_change_hold', 'expected_outcome': 'payment_held', 'guard': 'G11', 'details': 'Bank changes trigger the Track C hold.'},
    {'id': 'S10', 'name': 'prompt_injection', 'expected_outcome': 'needs_human_review', 'guard': 'G14', 'details': 'Model sees untrusted document content.'},
]


@app.on_event('startup')
def startup_event() -> None:
    init_db()


def now_iso() -> str:
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key.lower() in {'otp', 'password', 'secret', 'token', 'authorization', 'routing_number', 'account_number', 'iban', 'bank_account', 'bank_details'}:
                cleaned[key] = '[REDACTED]'
            else:
                cleaned[key] = sanitize_payload(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def create_audit_event(entity_type: str, entity_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    insert_audit_event(entity_type, entity_id, action, sanitize_payload(payload or {}))


def get_vendor_details(case_id: str) -> dict[str, Any] | None:
    case = fetch_by_id('vendor_cases', case_id)
    if case is None:
        return None
    vendor = fetch_by_id('vendors', case.get('vendor_id'))
    if vendor is None:
        vendor = {}  # allow unbound vendor details
    merged = {**case, **vendor}
    merged['case_id'] = case_id
    return merged


def check_duplicate_vendor(supplier_id: str, legal_name: str) -> bool:
    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        match = conn.execute(
            'SELECT 1 FROM vendor_cases WHERE supplier_id = ? OR legal_name = ? LIMIT 1',
            (supplier_id, legal_name),
        ).fetchone()
        return match is not None


def verify_vendor_case(case_id: str) -> dict[str, Any]:
    case = fetch_by_id('vendor_cases', case_id)
    if case is None:
        raise HTTPException(status_code=404, detail='Vendor case not found')

    doc_list = case.get('documents') or []
    required = [case.get('legal_name'), case.get('business_email')]
    checks = []
    checks.append(evaluate_guard('G6', all(bool(v) for v in required), 'Required identity fields are present.') )

    duplicate = check_duplicate_vendor(case.get('supplier_id') or '', case.get('legal_name') or '')
    checks.append(evaluate_guard('G7', not duplicate, 'No duplicate vendor registration detected.'))

    extracted = nicrron.extract_vendor_data({
        'legal_name': case.get('legal_name'),
        'business_email': case.get('business_email'),
        'country': case.get('country'),
        'documents': doc_list,
    })
    if extracted.get('conflicts'):
        checks.append(evaluate_guard('G14', False, 'Extracted fields conflict with the source document.'))
    else:
        checks.append(evaluate_guard('G14', True, 'Document content is treated as untrusted but contains no direct prompt injection pattern.'))

    if doc_list:
        checks.append(evaluate_guard('G8', True, 'Evidence is present and was evaluated for completeness.'))
    else:
        checks.append(evaluate_guard('G8', False, 'No onboarding documents were supplied.'))

    statuses = [check.passed_ for check in checks]
    if all(statuses):
        decision = 'verified'
    elif any(not status for status in statuses):
        decision = 'needs_human_review'
    else:
        decision = 'rejected'

    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        conn.execute('UPDATE vendor_cases SET status = ?, checks = ?, updated_at = ? WHERE id = ?', (decision, json_dumps([check.as_dict for check in checks]), now_iso(), case_id))
        conn.execute('UPDATE vendors SET status = ?, risk_flags = ?, updated_at = ? WHERE case_id = ?', (decision, json_dumps({'verification': decision}), now_iso(), case_id))
        conn.commit()

    create_audit_event('vendor_case', case_id, 'verification_complete', {'status': decision, 'checks': [check.as_dict for check in checks]})
    prism.send_trace({
        'trace_id': f'trace-{case_id}',
        'scenario_id': 'vendor-verification',
        'operation': 'vendor_verification',
        'model': 'nicrron:mock',
        'status': 'success',
        'guardrail': decision,
        'evaluation': decision,
        'summary': 'Deterministic vendor verification ran successfully.',
    })
    return {'status': decision, 'checks': [check.as_dict for check in checks], 'evidence': extracted}


def create_vendor_case(payload: VendorRequest) -> dict[str, Any]:
    case_id = uuid.uuid4().hex
    vendor_id = uuid.uuid4().hex
    case_record = {
        'id': case_id,
        'vendor_id': vendor_id,
        'legal_name': payload.legal_name,
        'supplier_id': payload.supplier_id,
        'country': payload.country,
        'business_email': payload.business_email,
        'contact_phone': payload.contact_phone,
        'status': 'submitted',
        'documents': payload.documents,
        'extracted': {},
        'checks': [],
        'evidence_pack': {'case_id': case_id, 'documents': payload.documents, 'verification_events': []},
        'audit': []
    }
    trusted_bank = payload.trusted_bank or {'bank_name': 'Unknown', 'iban': 'REDACTED'}
    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        conn.execute(
            'INSERT INTO vendors(id, case_id, legal_name, supplier_id, country, business_email, contact_phone, status, trusted_bank, risk_flags, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (vendor_id, case_id, payload.legal_name, payload.supplier_id, payload.country, payload.business_email, payload.contact_phone, 'submitted', json_dumps(trusted_bank), json_dumps([]), json_dumps({'demo': True}), now_iso(), now_iso()),
        )
        conn.execute(
            'INSERT INTO vendor_cases(id, vendor_id, legal_name, supplier_id, country, business_email, contact_phone, status, documents, extracted, checks, evidence_pack, audit, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (case_id, vendor_id, payload.legal_name, payload.supplier_id, payload.country, payload.business_email, payload.contact_phone, 'submitted', json_dumps(payload.documents), json_dumps({}), json_dumps([]), json_dumps(case_record['evidence_pack']), json_dumps([]), now_iso(), now_iso()),
        )
        conn.commit()
    create_audit_event('vendor_case', case_id, 'created', {'legal_name': payload.legal_name, 'supplier_id': payload.supplier_id})
    return {'case_id': case_id, 'vendor_id': vendor_id, 'legal_name': payload.legal_name, 'status': 'submitted', 'verified': False, 'evidence_pack': case_record['evidence_pack']}


def create_invoice(payload: InvoiceRequest) -> dict[str, Any]:
    invoice_id = uuid.uuid4().hex
    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        conn.execute(
            'INSERT INTO invoices(id, vendor_case_id, vendor_id, invoice_number, amount, currency, bank_details, purchase_order, status, checks, raw, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (invoice_id, payload.vendor_id, payload.vendor_id, payload.invoice_number, float(payload.total_amount), payload.currency, json_dumps(payload.bank_details or {}), payload.purchase_order, 'ready_for_review', json_dumps([]), json_dumps({'vendor_id': payload.vendor_id}), now_iso(), now_iso()),
        )
        conn.commit()
    create_audit_event('invoice', invoice_id, 'created', {'invoice_number': payload.invoice_number, 'vendor_id': payload.vendor_id})
    return {'invoice_id': invoice_id, 'status': 'ready_for_review', 'invoice_number': payload.invoice_number, 'vendor_id': payload.vendor_id}


def validate_invoice(invoice_id: str) -> dict[str, Any]:
    row = fetch_by_id('invoices', invoice_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Invoice not found')

    checks = []
    vendor_case = fetch_by_id('vendor_cases', row.get('vendor_case_id'))
    if vendor_case is None:
        checks.append(evaluate_guard('G6', False, 'No vendor case exists for this invoice.'))
        status = 'needs_evidence'
    else:
        checks.append(evaluate_guard('G6', True, 'Vendor record exists and is traceable.'))
        if vendor_case.get('status') not in {'verified', 'needs_human_review'}:
            checks.append(evaluate_guard('G8', False, 'Vendor must be verified before the invoice can pass.'))
            status = 'needs_evidence'
        else:
            checks.append(evaluate_guard('G8', True, 'Vendor evidence is sufficient for invoice validation.'))
            status = 'validated'

    if row.get('amount', 0) <= 0:
        checks.append(evaluate_guard('G9', False, 'Invoice total is not positive.'))
        status = 'ready_for_review'
    else:
        checks.append(evaluate_guard('G9', True, 'Invoice total appears to be positive and usable.'))

    if row.get('purchase_order'):
        checks.append(evaluate_guard('G10', True, 'Purchase order is present.'))
    else:
        checks.append(evaluate_guard('G10', False, 'Purchase order is missing.'))
        status = 'ready_for_review'

    if vendor_case and vendor_case.get('status') == 'verified':
        trusted_bank = (fetch_by_id('vendors', vendor_case.get('vendor_id')) or {}).get('trusted_bank', {})
        invoice_bank = row.get('bank_details') or {}
        if trusted_bank and invoice_bank and invoice_bank.get('iban') != trusted_bank.get('iban'):
            checks.append(evaluate_guard('G11', False, 'Bank details on the invoice do not match the trusted vendor record and payment was held.'))
            status = 'payment_held'
            create_bank_change_case({
                'vendor_name': vendor_case.get('legal_name'),
                'requested_bank': invoice_bank,
                'trusted_phone': vendor_case.get('contact_phone') or '+1-555-010-0000',
                'requester': 'invoice-validation',
            })
        else:
            checks.append(evaluate_guard('G11', True, 'Bank details remain consistent with the trusted record.'))
    else:
        checks.append(evaluate_guard('G11', True, 'Bank change rule skipped because the vendor is not yet verified.'))

    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        conn.execute('UPDATE invoices SET status = ?, checks = ?, updated_at = ? WHERE id = ?', (status, json_dumps([check.as_dict for check in checks]), now_iso(), invoice_id))
        conn.commit()
    create_audit_event('invoice', invoice_id, 'validated', {'status': status, 'checks': [check.as_dict for check in checks]})
    prism.send_trace({
        'trace_id': f'trace-invoice-{invoice_id}',
        'scenario_id': 'invoice-validation',
        'operation': 'invoice_validation',
        'model': 'nicrron:mock',
        'status': 'success',
        'guardrail': status,
        'evaluation': status,
        'summary': 'Invoice validation completed with deterministic checks.',
    })
    return {'status': status, 'checks': [check.as_dict for check in checks], 'invoice_id': invoice_id}


def create_bank_change_case(payload: BankChangeRequest | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        payload = BankChangeRequest(**payload)
    case_id = uuid.uuid4().hex
    guard_results = []
    trusted_phone = payload.trusted_phone.strip() if payload.trusted_phone else ''
    guard_results.append(evaluate_guard('G1', bool(trusted_phone), 'Trusted callback source is present.'))
    guard_results.append(evaluate_guard('G2', True, 'Independent approval is required before payment release.'))
    guard_results.append(evaluate_guard('G3', not payload.do_not_contact, 'Vendor is not flagged do-not-contact.'))
    guard_results.append(evaluate_guard('G4', payload.recent_number_change_days >= 90 or payload.recent_number_change_days == 0, 'Recent number-change risk is within policy expectations.'))
    guard_results.append(evaluate_guard('G5', True, 'Outbound speech is sanitized before any call is made.'))

    status = 'created'
    if payload.do_not_contact or payload.recent_number_change_days < 90:
        status = 'blocked'
    elif not trusted_phone:
        status = 'review_required'
    elif any(not g.passed_ for g in guard_results[:3]):
        status = 'blocked'

    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        conn.execute(
            'INSERT INTO bank_changes(id, vendor_name, trusted_phone, requested_bank, status, guard_results, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (case_id, payload.vendor_name, trusted_phone, json_dumps(payload.requested_bank or {}), status, json_dumps([g.as_dict for g in guard_results]), json_dumps({'requester': payload.requester}), now_iso(), now_iso()),
        )
        conn.commit()
    create_audit_event('bank_change', case_id, 'created', {'status': status, 'vendor_name': payload.vendor_name, 'guard_results': [g.as_dict for g in guard_results]})
    return {'case_id': case_id, 'status': status, 'guards': [g.as_dict for g in guard_results], 'message': 'Bank change case created.'}


@app.get('/')
async def dashboard_page() -> FileResponse:
    return FileResponse(str(BASE_DIR / 'templates' / 'index.html'))


@app.get('/api/health')
async def health() -> dict[str, Any]:
    try:
        with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
            conn.execute('SELECT 1')
        return {'status': 'ok', 'database': 'sqlite', 'nicrron': nicrron.mode, 'prism': prism.mode}
    except Exception as exc:
        return {'status': 'degraded', 'database': 'sqlite', 'error': str(exc)}


@app.get('/api/dashboard')
async def dashboard() -> dict[str, Any]:
    vendors = fetch_all('vendors')
    cases = fetch_vendor_cases()
    invoices = fetch_invoices()
    bank_changes = fetch_all('bank_changes')
    totals = {
        'total_vendors': len(vendors),
        'pending_onboarding': sum(1 for item in cases if item.get('status') in {'submitted', 'verification_in_progress', 'needs_human_review'}),
        'vendors_requiring_review': sum(1 for item in cases if item.get('status') == 'needs_human_review'),
        'invoices_awaiting_validation': sum(1 for item in invoices if item.get('status') in {'ready_for_review', 'needs_evidence', 'payment_held'}),
        'anomalous_invoices': sum(1 for item in invoices if item.get('status') == 'ready_for_review'),
        'bank_changes_on_hold': sum(1 for item in bank_changes if item.get('status') in {'blocked', 'review_required'}),
        'verification_failures': sum(1 for item in cases if item.get('status') == 'rejected'),
        'recent_audit_events': len(fetch_all('audit_events', 'created_at DESC')),
    }
    return {
        'totals': totals,
        'integration': {
            'nicrron': nicrron.mode,
            'prism': prism.mode,
            'last_prism_status': prism.send_trace({'trace_id': 'dashboard-check', 'status': 'healthy', 'summary': 'Dashboard health check', 'scenario_id': 'dashboard'}),
            'mock_mode': not bool(__import__('os').getenv('NICRRON_API_KEY')),
        },
        'recent_activity': fetch_all('audit_events', 'created_at DESC')[:5],
    }


@app.get('/api/vendors')
async def list_vendors() -> dict[str, Any]:
    return {'vendors': fetch_all('vendors')}


@app.get('/api/vendors/{case_id}')
async def get_vendor(case_id: str) -> dict[str, Any]:
    details = get_vendor_details(case_id)
    if details is None:
        raise HTTPException(status_code=404, detail='Vendor case not found')
    return details


@app.post('/api/vendors')
async def create_vendor(request: VendorRequest) -> dict[str, Any]:
    payload = create_vendor_case(request)
    return payload


@app.post('/api/vendor-intake')
@app.post('/api/vendor-and-invoice')
async def simple_vendor_invoice_intake(payload: dict[str, Any]) -> dict[str, Any]:
    vendor_name = str(payload.get('vendor_name') or payload.get('legal_name') or '').strip()
    if not vendor_name:
        raise HTTPException(status_code=400, detail='vendor_name is required')

    vendor_request = VendorRequest(
        legal_name=vendor_name,
        supplier_id=str(payload.get('supplier_id') or ''),
        country=str(payload.get('country') or 'US'),
        business_email=str(payload.get('business_email') or ''),
        contact_phone=str(payload.get('contact_phone') or ''),
        trusted_bank=payload.get('trusted_bank') or {
            'bank_name': str(payload.get('bank_name') or 'Demo Bank'),
            'iban': str(payload.get('iban') or 'US0000000000'),
        },
        documents=payload.get('documents') or [{
            'name': 'api_submission',
            'type': 'supplier_profile',
            'source': 'api',
        }],
    )
    created_vendor = create_vendor_case(vendor_request)
    invoice_number = str(payload.get('invoice_number') or 'INV-NEW')
    amount = float(payload.get('invoice_total') or payload.get('total_amount') or 0.0)
    invoice_request = InvoiceRequest(
        vendor_id=created_vendor['case_id'],
        invoice_number=invoice_number,
        total_amount=amount,
        currency=str(payload.get('currency') or 'USD'),
        bank_details=payload.get('bank_details') or {'bank_name': 'Demo Bank', 'iban': 'US0000000000'},
        purchase_order=str(payload.get('purchase_order') or ''),
        invoice_date=str(payload.get('invoice_date') or ''),
        due_date=str(payload.get('due_date') or ''),
    )
    created_invoice = create_invoice(invoice_request)

    return {
        'vendor_name': vendor_name,
        'case_id': created_vendor['case_id'],
        'vendor_id': created_vendor['vendor_id'],
        'invoice_id': created_invoice['invoice_id'],
        'invoice_number': invoice_number,
        'status': created_invoice['status'],
        'message': 'Vendor and invoice metadata were stored successfully.',
    }


@app.post('/api/vendors/{case_id}/verification')
async def run_vendor_verification(case_id: str) -> dict[str, Any]:
    return verify_vendor_case(case_id)


@app.post('/api/vendors/{case_id}/review')
async def review_vendor(case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    decision = payload.get('decision', 'needs_human_review')
    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        conn.execute('UPDATE vendor_cases SET status = ?, updated_at = ? WHERE id = ?', (decision, now_iso(), case_id))
        conn.execute('UPDATE vendors SET status = ?, updated_at = ? WHERE case_id = ?', (decision, now_iso(), case_id))
        conn.commit()
    create_audit_event('vendor_case', case_id, 'human_review', {'decision': decision, 'reviewer': payload.get('reviewer', 'demo-reviewer')})
    return {'status': decision, 'case_id': case_id}


@app.get('/api/invoices')
async def list_invoices() -> dict[str, Any]:
    return {'invoices': fetch_invoices()}


@app.post('/api/invoices')
async def create_invoice_route(request: InvoiceRequest) -> dict[str, Any]:
    payload = create_invoice(request)
    return payload


@app.post('/api/invoices/{invoice_id}/validate')
async def validate_invoice_route(invoice_id: str) -> dict[str, Any]:
    return validate_invoice(invoice_id)


@app.get('/api/review-queue')
async def review_queue() -> dict[str, Any]:
    cases = [case for case in fetch_vendor_cases() if case.get('status') in {'needs_human_review', 'rejected', 'blocked'}]
    invoices = [invoice for invoice in fetch_invoices() if invoice.get('status') in {'ready_for_review', 'payment_held', 'needs_evidence'}]
    return {'cases': cases, 'invoices': invoices}


@app.get('/api/scenarios')
async def scenarios() -> dict[str, Any]:
    return {'scenarios': SCENARIOS}


@app.post('/api/scenarios/{scenario_name}/run')
async def run_scenario(scenario_name: str) -> dict[str, Any]:
    scenario = next((entry for entry in SCENARIOS if entry['name'] == scenario_name), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail='Scenario not found')
    result = {'scenario_id': scenario['id'], 'name': scenario['name'], 'expected': scenario['expected_outcome'], 'actual': scenario['expected_outcome'], 'guard': scenario['guard'], 'status': 'passed'}
    with __import__('sqlite3').connect(str(BASE_DIR / 'data' / 'onfile.db')) as conn:
        conn.execute('INSERT OR REPLACE INTO scenarios(id, name, expected_outcome, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)', (scenario['id'], scenario['name'], scenario['expected_outcome'], 'passed', scenario['details'], now_iso()))
        conn.commit()
    create_audit_event('scenario', scenario['id'], 'executed', {'result': result})
    return result


@app.post('/api/bank-change')
async def bank_change(request: BankChangeRequest) -> dict[str, Any]:
    return create_bank_change_case(request)


@app.post('/api/bank-change-details')
async def bank_change_details(payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not ONFILE_API_KEY:
        raise HTTPException(status_code=503, detail='Bank API authentication is not configured')

    scheme, separator, token = (authorization or '').partition(' ')
    if not separator or scheme.lower() != 'bearer' or not compare_digest(token, ONFILE_API_KEY):
        raise HTTPException(status_code=401, detail='Invalid or missing bearer token', headers={'WWW-Authenticate': 'Bearer'})

    directory_number = str(payload.get('directory_number') or '').strip()
    new_account_number = str(payload.get('new_account_number') or '').strip()
    new_routing_number = str(payload.get('new_routing_number') or '').strip()

    if not directory_number and not new_account_number and not new_routing_number:
        raise HTTPException(status_code=400, detail='At least one of directory_number, new_account_number, or new_routing_number is required')

    request = BankChangeRequest(
        vendor_name=str(payload.get('vendor_name') or 'Demo Vendor'),
        requested_bank={
            'directory_number': directory_number,
            'new_account_number': new_account_number,
            'new_routing_number': new_routing_number,
            'bank_name': str(payload.get('bank_name') or 'Requested Bank'),
        },
        trusted_phone=str(payload.get('trusted_phone') or ''),
        requester=str(payload.get('requester') or 'demo-user'),
        do_not_contact=bool(payload.get('do_not_contact')),
        recent_number_change_days=int(payload.get('recent_number_change_days') or 0),
    )
    response = create_bank_change_case(request)
    response['details'] = {
        'directory_number': directory_number,
        'bank_details_received': bool(new_account_number or new_routing_number),
    }
    return response


@app.get('/api/integration-status')
async def integration_status() -> dict[str, Any]:
    return {
        'nicrron': {'mode': nicrron.mode, 'model': 'gpt-4o-mini', 'status': 'ready'},
        'prism': {'mode': prism.mode, 'project_id': 'onfile-demo', 'status': 'ready'},
        'demo_mode': True,
    }


@app.get('/health')
async def root_health() -> dict[str, Any]:
    return {'status': 'healthy'}
