import json
import os
import time
from typing import Any

import httpx

from app.config import NICRRON_API_KEY, NICRRON_BASE_URL, NICRRON_MODEL, PRISM_API_KEY, PRISM_API_URL, PRISM_PROJECT_ID


class NicrronClient:
    def __init__(self) -> None:
        self.mode = 'mock' if not NICRRON_API_KEY else 'live'

    def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not NICRRON_API_KEY:
            return self._mock_response(endpoint, payload)

        for attempt in range(3):
            try:
                response = httpx.post(
                    f'{NICRRON_BASE_URL.rstrip("/")}/{endpoint.lstrip("/")}',
                    json=payload,
                    headers={'Authorization': f'Bearer {NICRRON_API_KEY}', 'Content-Type': 'application/json'},
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError('Nicrron response was not a JSON object')
                return data
            except Exception:
                if attempt == 2:
                    return self._mock_response(endpoint, payload, live_error=True)
                time.sleep(0.5)
        return self._mock_response(endpoint, payload, live_error=True)

    def _mock_response(self, endpoint: str, payload: dict[str, Any], live_error: bool = False) -> dict[str, Any]:
        keyword = payload.get('document_type') or payload.get('task') or endpoint
        if 'vendor' in endpoint or 'vendor' in keyword:
            response = {
                'status': 'mock',
                'source': 'mock',
                'confidence': 0.92,
                'fields': {
                    'legal_name': payload.get('legal_name') or 'Demo Vendor',
                    'business_email': payload.get('business_email') or 'ops@demo-vendor.example',
                    'country': payload.get('country') or 'US',
                    'tax_identifier': payload.get('tax_identifier') or 'US-123456789',
                    'trusted_bank': payload.get('trusted_bank') or {'bank_name': 'Demo Bank', 'iban': 'US1234567890'},
                },
                'warnings': ['Synthetic mode: no live API key configured'],
                'missing_fields': [],
                'conflicts': [],
            }
        elif 'invoice' in endpoint or 'invoice' in keyword:
            response = {
                'status': 'mock',
                'source': 'mock',
                'confidence': 0.89,
                'fields': {
                    'invoice_number': payload.get('invoice_number') or 'INV-1000',
                    'currency': payload.get('currency') or 'USD',
                    'total_amount': payload.get('total_amount') or 500.0,
                    'vendor_name': payload.get('vendor_name') or 'Demo Vendor',
                },
                'warnings': ['Synthetic mode: no live API key configured'],
                'missing_fields': [],
                'conflicts': [],
            }
        else:
            response = {
                'status': 'mock',
                'source': 'mock',
                'task': endpoint,
                'result': 'synthetic fallback',
                'warnings': ['Synthetic mode: no live API key configured'],
            }
        if live_error:
            response['warnings'].append('Live API unavailable, fallback used for the demo')
        return response

    def extract_vendor_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('vendor/extract', payload)

    def extract_invoice_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('invoice/extract', payload)

    def explain_inconsistency(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request('inconsistency/explain', payload)


class PrismClient:
    def __init__(self) -> None:
        self.mode = 'mock' if not PRISM_API_KEY else 'live'

    def send_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        if not PRISM_API_KEY:
            return {
                'status': 'mock',
                'project_id': PRISM_PROJECT_ID,
                'trace_id': trace.get('trace_id', 'demo-trace'),
                'mode': 'mock',
                'message': 'Synthetic trace accepted without sending to PRISM.',
            }

        try:
            payload = {
                'project_id': PRISM_PROJECT_ID,
                'trace_id': trace.get('trace_id'),
                'scenario_id': trace.get('scenario_id'),
                'operation': trace.get('operation'),
                'model': trace.get('model') or NICRRON_MODEL,
                'duration_ms': trace.get('duration_ms'),
                'status': trace.get('status'),
                'guardrail': trace.get('guardrail'),
                'evaluation': trace.get('evaluation'),
                'error_category': trace.get('error_category'),
                'summary': trace.get('summary'),
                'sanitized': True,
            }
            response = httpx.post(PRISM_API_URL, json=payload, headers={'X-PRISMtrace-Key': PRISM_API_KEY, 'Content-Type': 'application/json'}, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            return {
                'status': 'unavailable',
                'project_id': PRISM_PROJECT_ID,
                'mode': 'mock',
                'error': str(exc),
                'message': 'PRISM was unavailable; verification continued safely.',
            }


nicrron = NicrronClient()
prism = PrismClient()
