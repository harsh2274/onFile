from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_dashboard_and_health_endpoints():
    health = client.get('/api/health')
    assert health.status_code == 200
    payload = health.json()
    assert 'status' in payload
    assert 'database' in payload

    root = client.get('/')
    assert root.status_code == 200
    assert 'OnFile' in root.text

    dashboard = client.get('/api/dashboard')
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert 'totals' in body
    assert 'integration' in body


def test_vendor_case_and_verification_flow():
    response = client.post('/api/vendors', json={
        'legal_name': 'Northwind Logistics',
        'supplier_id': 'NWL-1001',
        'country': 'US',
        'business_email': 'ops@northwind.example',
        'contact_phone': '+1-555-010-1000',
        'trusted_bank': {'iban': 'US1234567890', 'bank_name': 'Test Bank'},
        'documents': [{'name': 'registration.pdf', 'type': 'registration', 'source': 'upload'}],
    })
    assert response.status_code == 200, response.text
    vendor = response.json()
    assert vendor['legal_name'] == 'Northwind Logistics'
    case_id = vendor['case_id']

    verify = client.post(f'/api/vendors/{case_id}/verification')
    assert verify.status_code == 200
    payload = verify.json()
    assert payload['status'] in {'verified', 'needs_human_review', 'rejected', 'blocked'}


def test_invoice_validation_handles_bank_change_and_hold():
    vendor = client.post('/api/vendors', json={
        'legal_name': 'Blue Harbor Supply',
        'supplier_id': 'BHS-2002',
        'country': 'GB',
        'business_email': 'payables@blueharbor.example',
        'contact_phone': '+44-20-5555-0131',
        'trusted_bank': {'iban': 'GB1234567890', 'bank_name': 'Blue Bank'},
        'documents': [{'name': 'vendor_profile.pdf', 'type': 'supplier_profile', 'source': 'upload'}],
    }).json()

    invoice = client.post('/api/invoices', json={
        'vendor_id': vendor['case_id'],
        'invoice_number': 'BH-2026-001',
        'total_amount': 1500.00,
        'currency': 'GBP',
        'bank_details': {'iban': 'GB9999999999', 'bank_name': 'Fraud Bank'},
        'purchase_order': 'PO-4401',
        'invoice_date': '2026-10-17',
        'due_date': '2026-10-31',
    })
    assert invoice.status_code == 200, invoice.text
    payload = invoice.json()
    assert payload['status'] in {'ready_for_review', 'holds_payment', 'payment_held', 'needs_evidence'}

    validation = client.post(f"/api/invoices/{payload['invoice_id']}/validate")
    assert validation.status_code == 200
    result = validation.json()
    assert 'checks' in result or 'status' in result


def test_bank_change_workflow_rejects_invalid_transition():
    result = client.post('/api/bank-change', json={
        'vendor_name': 'Acme Manufacturing',
        'requested_bank': {'iban': 'US7777777', 'bank_name': 'Attacker Bank'},
        'trusted_phone': '+1-555-010-9999',
        'requester': 'Fraudster',
    })
    assert result.status_code == 200
    body = result.json()
    assert 'status' in body
    assert body['status'] in {'created', 'review_required', 'blocked'}


def test_bank_change_details_requires_bearer_key_and_redacts_bank_numbers(monkeypatch):
    body = {
        'directory_number': '123456',
        'new_account_number': '9876543210',
        'new_routing_number': '021000021',
    }

    monkeypatch.setattr('app.main.ONFILE_API_KEY', '')
    unconfigured = client.post('/api/bank-change-details', json=body)
    assert unconfigured.status_code == 503

    monkeypatch.setattr('app.main.ONFILE_API_KEY', 'test-only-bearer-key')
    unauthorized = client.post('/api/bank-change-details', json=body)
    assert unauthorized.status_code == 401

    authorized = client.post(
        '/api/bank-change-details',
        json=body,
        headers={'Authorization': 'Bearer test-only-bearer-key'},
    )
    assert authorized.status_code == 200, authorized.text
    response_text = authorized.text
    assert body['new_account_number'] not in response_text
    assert body['new_routing_number'] not in response_text
    assert authorized.json()['details'] == {
        'directory_number': '123456',
        'bank_details_received': True,
    }


def test_vendor_and_invoice_intake_api():
    response = client.post('/api/vendor-intake', json={
        'vendor_name': 'Harbor Foods',
        'supplier_id': 'HF-440',
        'country': 'US',
        'business_email': 'accounts@harborfoods.example',
        'invoice_number': 'HF-INV-1042',
        'invoice_total': 2450.75,
        'currency': 'USD',
        'invoice_date': '2026-10-17',
        'due_date': '2026-10-31',
        'purchase_order': 'PO-2019',
        'bank_details': {'iban': 'US5555555555', 'bank_name': 'Demo Bank'}
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert 'case_id' in payload
    assert 'invoice_id' in payload
    assert payload['vendor_name'] == 'Harbor Foods'


def test_scenarios_endpoint_renders_registry():
    scenario = client.get('/api/scenarios')
    assert scenario.status_code == 200
    data = scenario.json()
    assert 'scenarios' in data
    assert len(data['scenarios']) >= 10
