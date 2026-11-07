# Deploy to Azure — copy-paste runbook

Run in **Azure Cloud Shell (Bash)**. Resources from `docs/azure-setup.md` Sections 1–4 (resource group, storage, Azure OpenAI + deployments) must already exist. This file covers everything after that: registry, environment, image, daily job, web app.

One image, two roles: the **job** (`MODE=job`) runs the pipeline and writes results to Blob; the **web app** (`MODE=app`) reads Blob and serves the page + API. Both need the same settings.

> Never paste keys into chat or files. The commands below read them into shell variables only.

---

## 0. Variables (run first, and again after any Cloud Shell restart)

```bash
az account set --subscription unc-sdis-solveathon-team01
az account show --query "{name:name, user:user.name}" -o table   # must say unc-sdis-solveathon-team01

export SUFFIX="10352" RG="rg-ffm-team" LOCATION="eastus"
export STORAGE="stffm${SUFFIX}" AOAI="aoai-ffm-${SUFFIX}" ACR="acrffm${SUFFIX}"
export CAE="cae-ffm" APP="app-ffm-web" JOB="job-ffm-refresh"
export TAG="v1"   # bump for every new build: v2, v3, ...
export IMAGE="$ACR.azurecr.io/ffm:$TAG"

# secrets -> shell variables only (nothing is printed)
export STORAGE_CONN=$(az storage account show-connection-string -n $STORAGE -g $RG --query connectionString -o tsv)
export AOAI_ENDPOINT=$(az cognitiveservices account show -n $AOAI -g $RG --query properties.endpoint -o tsv)
export AOAI_KEY=$(az cognitiveservices account keys list -n $AOAI -g $RG --query key1 -o tsv)
echo "storage conn: ${#STORAGE_CONN} chars | aoai key: ${#AOAI_KEY} chars | endpoint: $AOAI_ENDPOINT"
```

✅ Both lengths are non-zero and the endpoint is `https://aoai-ffm-10352.openai.azure.com/`.

---

## 1. Container Registry + Container Apps environment (once)

```bash
az extension add --name containerapp --upgrade --only-show-errors

az acr show -n $ACR -g $RG -o none 2>/dev/null || \
  az acr create -n $ACR -g $RG -l $LOCATION --sku Basic --admin-enabled true -o table

az containerapp env show -n $CAE -g $RG -o none 2>/dev/null || \
  az containerapp env create -n $CAE -g $RG -l $LOCATION -o table

export ACR_USER=$(az acr credential show -n $ACR --query username -o tsv)
export ACR_PASS=$(az acr credential show -n $ACR --query "passwords[0].value" -o tsv)
az containerapp env show -n $CAE -g $RG --query properties.provisioningState -o tsv   # Succeeded
```

---

## 2. Build the image in the cloud (from the latest `main`)

```bash
cd ~ && rm -rf Solve-a-thon-2026
git clone --depth 1 https://github.com/ErHxyun/Solve-a-thon-2026.git
cd Solve-a-thon-2026 && git log --oneline -1          # confirm it is the commit you expect
az acr build --registry $ACR --image ffm:$TAG .
az acr repository show-tags -n $ACR --repository ffm -o table
```

`.dockerignore` keeps `.env`, `data/` and `logs/` out of the image.

---

## 3. Settings shared by the job and the app

```bash
SECRETS="storage-conn=$STORAGE_CONN aoai-key=$AOAI_KEY"
COMMON_ENV="STORAGE_MODE=blob STORAGE_CONN=secretref:storage-conn STORAGE_CONTAINER=ffm-data \
AOAI_ENDPOINT=$AOAI_ENDPOINT AOAI_KEY=secretref:aoai-key AOAI_API_VERSION=2025-04-01-preview \
AOAI_CHAT_DEPLOYMENT=chat AOAI_FALLBACK_DEPLOYMENT=chat-fallback AOAI_EMBED_DEPLOYMENT=embed \
LLM_ENABLED=true COLLECTOR_MODE=live AGENCY=dncr TOP_N=15"
```

The three that are easy to forget: **`STORAGE_MODE=blob`** (otherwise each container writes to its own disk and the app shows nothing), **`LLM_ENABLED=true`** (otherwise offline placeholder cards), **`COLLECTOR_MODE=live`** (otherwise the 40-record test fixture).

---

## 4. Daily job — create, then run once NOW (fills Blob before the app starts)

```bash
az containerapp job create -n $JOB -g $RG --environment $CAE \
  --trigger-type Schedule --cron-expression "0 10 * * *" \
  --replica-timeout 3600 --replica-retry-limit 1 --replica-completion-count 1 --parallelism 1 \
  --image $IMAGE --registry-server $ACR.azurecr.io --registry-username $ACR_USER --registry-password $ACR_PASS \
  --cpu 1.0 --memory 2.0Gi \
  --secrets $SECRETS \
  --env-vars MODE=job $COMMON_ENV -o table

az containerapp job start -n $JOB -g $RG -o table
```

Watch it (first run ≈ 4–6 min: 1,364 detail fetches, then 15 GPT-6 cards):

```bash
az containerapp job execution list -n $JOB -g $RG \
  --query "[].{name:name, status:properties.status, start:properties.startTime}" -o table
```

✅ Latest execution is `Succeeded`. Check Blob got the results:

```bash
az storage blob list -c ffm-data --connection-string "$STORAGE_CONN" \
  --prefix runs/ --query "[].name" -o tsv | tail -3
az storage blob download -c ffm-data --connection-string "$STORAGE_CONN" -n runs/latest.json -f /tmp/run.json -o none && cat /tmp/run.json
```

`status: succeeded`, `opportunities` ≈ 1,300+, `cards` = 15.

If it says `Failed`: see Section 7.

---

## 5. Web app

```bash
az containerapp create -n $APP -g $RG --environment $CAE \
  --image $IMAGE --registry-server $ACR.azurecr.io --registry-username $ACR_USER --registry-password $ACR_PASS \
  --ingress external --target-port 8000 \
  --cpu 1.0 --memory 2.0Gi --min-replicas 1 --max-replicas 1 \
  --secrets $SECRETS \
  --env-vars MODE=app $COMMON_ENV -o table

export URL="https://$(az containerapp show -n $APP -g $RG --query properties.configuration.ingress.fqdn -o tsv)"
echo $URL
curl -s $URL/api/health; echo
curl -s "$URL/api/shortlist?limit=3" | head -c 600; echo
```

✅ `/api/health` → `{"ok":true,"storage":"blob","llm_enabled":true}`; the short list has items with cards.
Open `$URL` (the page) and `$URL/docs` on a laptop **and on a phone hotspot**. Put the URL in the team doc.

---

## 6. Ship new code (e.g. Kun Yao's page, a scoring fix)

```bash
export TAG="v2" IMAGE="$ACR.azurecr.io/ffm:v2"     # new tag every time
cd ~/Solve-a-thon-2026 && git pull && az acr build --registry $ACR --image ffm:$TAG .
az containerapp update     -n $APP -g $RG --image $IMAGE -o table
az containerapp job update -n $JOB -g $RG --image $IMAGE -o table
az containerapp job start  -n $JOB -g $RG -o table      # optional: refresh data now
```

Changing a setting only (no rebuild): `az containerapp update -n $APP -g $RG --set-env-vars TOP_N=20` (same for `job update`).

---

## 7. Troubleshooting

```bash
# App logs (live)
az containerapp logs show -n $APP -g $RG --tail 80 --follow

# Job logs: find the latest execution name, then read its console output
az containerapp job execution list -n $JOB -g $RG --query "[0].name" -o tsv
az containerapp job logs show -n $JOB -g $RG --container $JOB --execution <execution-name> --tail 200
```

| Symptom | Likely cause | Fix |
|---|---|---|
| Page loads, short list empty | Job hasn't succeeded yet, or `STORAGE_MODE` isn't `blob` | Section 4; check `/api/health` says `"storage":"blob"` |
| Cards say `[offline placeholder]` | `LLM_ENABLED` not `true`, or key wrong | `/api/health`; re-run Section 0 then `az containerapp update ... --replace-env-vars` with Section 3 |
| Only ~40 opportunities | `COLLECTOR_MODE` not `live` | Section 3 |
| Job `Failed`, logs mention `api.grants.gov` | Outbound network | Retry; the collector retries 4× per call and caches details |
| `401` from Azure OpenAI | Key regenerated after deploy | Re-run Section 0, then `az containerapp secret set -n $APP -g $RG --secrets aoai-key=$AOAI_KEY` (and for the job), then restart |
| App crashes on start | Bad image / port | Logs; `--target-port 8000`; `MODE=app` starts uvicorn |

**After judging only (team agrees):** `az group delete -n $RG --yes --no-wait`
