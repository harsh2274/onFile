# OnFile

A local, SQLite-backed vendor onboarding and payment-risk verification demo with a guarded state machine, mock Nicrron integration, and synthetic PRISM traces.

## Local setup

Use the required conda environment throughout:

```powershell
conda activate myenv
cd C:\Users\Asus\Desktop\onfile
python -m pip install -r requirements.txt
```

## Run the app

```powershell
conda activate myenv
cd C:\Users\Asus\Desktop\onfile
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

- http://127.0.0.1:8000/
- http://127.0.0.1:8000/api/health

## Optional live integrations

Copy the sample env file and set real values only when available:

```powershell
copy .env.example .env
```

Supported variables:

- `NICRRON_API_KEY`
- `NICRRON_BASE_URL`
- `NICRRON_MODEL`
- `PRISM_API_KEY`
- `PRISM_API_URL`
- `PRISM_PROJECT_ID`

If keys are absent, the demo stays in mock/synthetic mode and reports the integration status clearly.

## Verification

```powershell
conda activate myenv
cd C:\Users\Asus\Desktop\onfile
python -m pytest -q
```
