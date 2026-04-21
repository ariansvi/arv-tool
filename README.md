# arv-tool

Calculate **After-Repair Value (ARV)** for a real-estate property from a Zillow URL.

1. Paste a Zillow `homedetails` URL.
2. We parse the address from the URL.
3. [Rentcast](https://app.rentcast.io/app/api) gives us recent comparable sales and an AVM.
4. [Cuyahoga County MyPlace](https://myplace.cuyahogacounty.gov) data is used to cross-check each comp's sqft / beds / year built.
5. ARV = median $/sqft of comps × subject sqft (with high/low outliers trimmed when ≥5 comps).

Cleveland / Cuyahoga County is the only county with automated MyPlace verification.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # then fill in RENTCAST_API_KEY
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000.

## Deploy to Cloud Run

```bash
# One-time: create a secret holding the API key
gcloud secrets create rentcast-api-key --replication-policy=automatic
echo -n "$RENTCAST_API_KEY" | gcloud secrets versions add rentcast-api-key --data-file=-

# Deploy
gcloud run deploy arv-tool \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets RENTCAST_API_KEY=rentcast-api-key:latest
```
