# Azure Setup Guide — Federal Funds Matchmaker (DNCR)

Step-by-step instructions for creating every Azure resource our prototype needs, written for **our** subscription. Run the commands in **Azure Cloud Shell (Bash)**: Azure portal → the `>_` icon at the top right.

> **Owner:** P2 (Data & Cloud). Anyone can follow it.
> **Rule:** never paste keys or connection strings into the repo, the team Google Doc, or chat. Share them only in a private message.

---

## 0. What we know about our environment

Checked on 2026-11-07 with `az role assignment list` / `az policy assignment list` / `az network vnet subnet list`.

| Item | Value | Meaning for us |
|---|---|---|
| Subscription | `unc-sdis-solveathon-team01` | **Always** deploy here |
| Region of pre-built network | East US | We use `eastus` for everything |
| Our role | **Contributor** (subscription, via group) | We can create all resources ourselves |
| Extra roles | Cognitive Services OpenAI User · Key Vault Secrets Officer · Constrained User Access Administrator | We can call Azure OpenAI, use Key Vault, and assign limited roles (managed identity = roadmap) |
| Policies visible | None | No restrictions found at subscription level (a parent policy could still appear as an error) |
| Pre-built resources | `spoke` VNet, `its-snet` (10.0.0.0/24, no delegation), NAT gateway, NSG, public IP, Network Watcher, alerts | Organizer networking. **Do not modify. Do not put Container Apps in `its-snet`.** |
| Azure OpenAI | None yet | We create our own (Step 3) |
| ⚠️ `Azure subscription 1` (Green-Leaves etc.) | A personal subscription | **Never** create hackathon resources there; it may bill a personal account |

---

## 1. Set variables (re-run every time Cloud Shell restarts)

Cloud Shell is **ephemeral**: when it disconnects, all variables are lost. The first time, run this block and **write the printed `SUFFIX` in the team doc**. Afterwards, replace `$RANDOM` with that number so the names stay the same.

```bash
az account set --subscription unc-sdis-solveathon-team01

export PREFIX="ffm"
export LOCATION="eastus"
export SUFFIX="$RANDOM"          # first time only; afterwards e.g. SUFFIX="12345"
export RG="rg-$PREFIX-team"
export STORAGE="st${PREFIX}${SUFFIX}"
export AOAI="aoai-$PREFIX-$SUFFIX"
export ACR="acr${PREFIX}${SUFFIX}"
export CAE="cae-$PREFIX"
export APP="app-$PREFIX-web"
export JOB="job-$PREFIX-refresh"
export IMAGE="$ACR.azurecr.io/ffm:v1"

echo "SUFFIX=$SUFFIX"
az account show --query "{name:name, user:user.name}" -o table
```

✅ **Check:** the table shows `unc-sdis-solveathon-team01`.

> **SUFFIX:** `10352` → after a restart use `export SUFFIX="10352"` (not `$RANDOM`)
> Naming rule: storage account and registry names must be globally unique, lowercase letters and digits only, no dashes. The suffix takes care of that.

---

## 2. Register providers and create the resource group

```bash
az extension add --name containerapp --upgrade --only-show-errors

for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry \
          Microsoft.Storage Microsoft.CognitiveServices; do
  az provider register --namespace $ns --wait
done

az group create --name $RG --location $LOCATION -o table
```

✅ **Check:**
```bash
az group show -n $RG --query properties.provisioningState -o tsv   # Succeeded
```

If `provider register` gives a permission error, skip it and continue. Providers are often already registered.

---

## 3. Blob Storage (all our JSON data)

```bash
az storage account create -n $STORAGE -g $RG -l $LOCATION \
  --sku Standard_LRS --kind StorageV2 \
  --min-tls-version TLS1_2 --allow-blob-public-access false -o table

export STORAGE_CONN=$(az storage account show-connection-string \
  -n $STORAGE -g $RG --query connectionString -o tsv)

az storage container create --name ffm-data --connection-string "$STORAGE_CONN"
```

✅ **Check:**
```bash
az storage container list --connection-string "$STORAGE_CONN" --query "[].name" -o tsv   # ffm-data
```

---

## 4. Azure OpenAI (chat model + embedding model)

### 4a. Create the resource

```bash
az cognitiveservices account create -n $AOAI -g $RG -l $LOCATION \
  --kind OpenAI --sku S0 --custom-domain $AOAI --yes -o table
```

### 4b. See which models this region offers

```bash
az cognitiveservices account list-models -n $AOAI -g $RG \
  --query "[?contains(name,'mini') || contains(name,'embedding')].{model:name, version:version}" -o table
```

Pick:
- **Chat:** a small "mini" model (e.g., `gpt-4o-mini`) and one of its listed versions
- **Embedding:** `text-embedding-3-small`, version `1`

### 4c. Deploy both models

Use the names `chat` and `embed`; our code expects exactly these.

```bash
az cognitiveservices account deployment create -n $AOAI -g $RG \
  --deployment-name embed --model-name text-embedding-3-small \
  --model-version 1 --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 50

# Replace model name/version with what 4b showed
az cognitiveservices account deployment create -n $AOAI -g $RG \
  --deployment-name chat --model-name gpt-4o-mini \
  --model-version 2024-07-18 --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 50
```

If `GlobalStandard` is rejected, retry with `--sku-name Standard`. If the capacity is rejected, lower `--sku-capacity` (e.g., 10).

### 4d. Read endpoint and key into variables (never echo the key)

```bash
export AOAI_ENDPOINT=$(az cognitiveservices account show -n $AOAI -g $RG --query properties.endpoint -o tsv)
export AOAI_KEY=$(az cognitiveservices account keys list -n $AOAI -g $RG --query key1 -o tsv)
export AOAI_API_VERSION="2024-10-21"
echo $AOAI_ENDPOINT
```

✅ **Check 1:** both deployments succeeded:
```bash
az cognitiveservices account deployment list -n $AOAI -g $RG \
  --query "[].{name:name, model:properties.model.name, state:properties.provisioningState}" -o table
```

✅ **Check 2:** the chat model answers:
```bash
curl -s "$AOAI_ENDPOINT/openai/deployments/chat/chat/completions?api-version=$AOAI_API_VERSION" \
  -H "Content-Type: application/json" -H "api-key: $AOAI_KEY" \
  -d '{"messages":[{"role":"user","content":"Reply with the word pong"}],"max_tokens":5}' \
  | head -c 400; echo
```

✅ **Check 3:** embeddings work (a long list of numbers means success):
```bash
curl -s "$AOAI_ENDPOINT/openai/deployments/embed/embeddings?api-version=$AOAI_API_VERSION" \
  -H "Content-Type: application/json" -H "api-key: $AOAI_KEY" \
  -d '{"input":"state parks trails"}' | head -c 200; echo
```

> If creation fails with `RequestDisallowedByPolicy` or "local authentication disabled", a parent policy blocks key auth. Stop and switch to Entra ID authentication (we have the *OpenAI User* role). Record it in the Lessons Log.

---

## 5. Smoke-test Grants.gov (no key needed)

```bash
curl -s -X POST https://api.grants.gov/v1/api/search2 \
  -H "Content-Type: application/json" \
  -d '{"keyword":"historic preservation","oppStatuses":"forecasted|posted","rows":5}' \
  | head -c 1500; echo
```

✅ **Check:** JSON with `hitCount` and opportunities (id, number, title, agency, dates).

---

## 6. Container Registry + Container Apps environment

```bash
az acr create -n $ACR -g $RG -l $LOCATION --sku Basic --admin-enabled true -o table

# No VNet integration on purpose (its-snet is the organizers' and too small for Container Apps)
az containerapp env create -n $CAE -g $RG -l $LOCATION -o table

export ACR_USER=$(az acr credential show -n $ACR --query username -o tsv)
export ACR_PASS=$(az acr credential show -n $ACR --query "passwords[0].value" -o tsv)
```

✅ **Check:**
```bash
az containerapp env show -n $CAE -g $RG --query properties.provisioningState -o tsv   # Succeeded
```

> 🏁 **Milestone:** Steps 1–6 finished = all infrastructure exists. Steps 7–9 need our code.

---

## 7. Hand configuration to teammates (local development)

Each developer creates a `.env` file in the repo root. **`.env` must be listed in `.gitignore`.**

```bash
AOAI_ENDPOINT=https://<aoai-name>.openai.azure.com/
AOAI_KEY=<send privately, never commit>
AOAI_API_VERSION=2024-10-21
AOAI_CHAT_DEPLOYMENT=chat
AOAI_EMBED_DEPLOYMENT=embed
STORAGE_MODE=local            # use ./data locally; "blob" in Azure
STORAGE_CONN=<send privately, only if needed locally>
```

To print values for a private message (run in Cloud Shell, then copy):
```bash
echo "AOAI_ENDPOINT=$AOAI_ENDPOINT"; echo "AOAI_KEY=$AOAI_KEY"
```

---

## 8. Build the image in the cloud (after code exists)

The repo needs two files at its root.

**`Dockerfile`**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x entrypoint.sh
EXPOSE 8000
CMD ["./entrypoint.sh"]
```

**`entrypoint.sh`**
```bash
#!/bin/sh
set -e
if [ "$MODE" = "job" ]; then
  python pipeline.py
else
  streamlit run app/streamlit_app.py --server.port 8000 --server.address 0.0.0.0
fi
```

Get the code into Cloud Shell (`git clone <repo-url>`, then `cd` into it), then:
```bash
az acr build --registry $ACR --image ffm:v1 .
```

✅ **Check:**
```bash
az acr repository show-tags -n $ACR --repository ffm -o table   # v1
```

---

## 9. Deploy the web app

```bash
az containerapp create -n $APP -g $RG --environment $CAE \
  --image $IMAGE \
  --registry-server $ACR.azurecr.io --registry-username $ACR_USER --registry-password $ACR_PASS \
  --ingress external --target-port 8000 \
  --cpu 1.0 --memory 2.0Gi --min-replicas 1 --max-replicas 1 \
  --secrets storage-conn="$STORAGE_CONN" aoai-key="$AOAI_KEY" \
  --env-vars MODE=app STORAGE_MODE=blob STORAGE_CONN=secretref:storage-conn \
             AOAI_ENDPOINT=$AOAI_ENDPOINT AOAI_KEY=secretref:aoai-key \
             AOAI_API_VERSION=$AOAI_API_VERSION \
             AOAI_CHAT_DEPLOYMENT=chat AOAI_EMBED_DEPLOYMENT=embed

echo "https://$(az containerapp show -n $APP -g $RG --query properties.configuration.ingress.fqdn -o tsv)"
```

✅ **Check:** open the URL on a laptop **and** on a phone hotspot. Write it in the team doc.

---

## 10. Daily refresh job (6 AM Eastern = 10:00 UTC)

```bash
az containerapp job create -n $JOB -g $RG --environment $CAE \
  --trigger-type Schedule --cron-expression "0 10 * * *" \
  --replica-timeout 3600 --replica-retry-limit 1 \
  --replica-completion-count 1 --parallelism 1 \
  --image $IMAGE \
  --registry-server $ACR.azurecr.io --registry-username $ACR_USER --registry-password $ACR_PASS \
  --cpu 1.0 --memory 2.0Gi \
  --secrets storage-conn="$STORAGE_CONN" aoai-key="$AOAI_KEY" \
  --env-vars MODE=job STORAGE_MODE=blob STORAGE_CONN=secretref:storage-conn \
             AOAI_ENDPOINT=$AOAI_ENDPOINT AOAI_KEY=secretref:aoai-key \
             AOAI_API_VERSION=$AOAI_API_VERSION \
             AOAI_CHAT_DEPLOYMENT=chat AOAI_EMBED_DEPLOYMENT=embed

# Run once now
az containerapp job start -n $JOB -g $RG
```

✅ **Check:**
```bash
az containerapp job execution list -n $JOB -g $RG \
  --query "[].{name:name, status:properties.status, start:properties.startTime}" -o table   # Succeeded
```
Then refresh the app and confirm "Last refreshed" changed.

---

## 11. Everyday operations

```bash
# Ship new code: build a new tag, roll out to both roles
az acr build --registry $ACR --image ffm:v2 .
az containerapp update     -n $APP -g $RG --image $ACR.azurecr.io/ffm:v2
az containerapp job update -n $JOB -g $RG --image $ACR.azurecr.io/ffm:v2

# Live app logs
az containerapp logs show -n $APP -g $RG --tail 50 --follow

# What's in storage?
az storage blob list -c ffm-data --connection-string "$STORAGE_CONN" --query "[].name" -o tsv
```

**Troubleshooting**

| Symptom | Likely cause | Fix |
|---|---|---|
| Variables empty / "resource not found" | Cloud Shell restarted | Re-run Section 1 with the saved SUFFIX, then the `export` lines in 3, 4d, 6 |
| App URL errors or times out | Wrong port or app crashed | Check logs; port must be 8000; `MODE=app` must start Streamlit |
| Job status Failed | Missing env var or exception | Run `python pipeline.py` locally with the same `.env`; read logs |
| `acr build` fails | Wrong folder or dependency | Run from the repo root; pin versions in `requirements.txt` |
| OpenAI 429 | Too many calls | LLM on top 15 only; cache embeddings; retry with backoff; lower concurrency |
| Grants.gov returns nothing | Filters too narrow | Include forecasted; broaden keywords; raise rows; page through results |

**Teardown (after judging only, and only after the team agrees):**
```bash
az group delete -n $RG --yes --no-wait
```
This deletes only *our* resource group, not the organizers' networking resources.

---

## 12. Resource inventory (fill in as you go)

| Resource | Name | Status |
|---|---|---|
| Resource group | `rg-ffm-team` | ✅ 2026-11-07 |
| Storage account | `stffm10352` + container `ffm-data` | ✅ 2026-11-07 |
| Azure OpenAI | `aoai-ffm-10352` (eastus) | ✅ 2026-11-07 |
| Chat deployment | `chat` → gpt-6.1-terra 2026-10-26 | ✅ 2026-11-07 |
| Candidate | `chat-sol` → gpt-6.1-sol 2026-09-29 | ✅ 2026-11-07 |
| Candidate | `chat-luna` → gpt-6-luna 2026-09-22 | ✅ 2026-11-07 |
| Candidate | `chat-astra` → gpt-6-astra 2026-09-03 | ✅ 2026-11-07 |
| Fallback chat deployment | `chat-fallback` → gpt-4.1 2025-04-14 | ✅ 2026-11-07 |
| Embedding deployment | `embed` → text-embedding-3-large v1 | ✅ 2026-11-07 |

**Smoke-test results (2026-11-07, api-version 2025-04-01-preview):**

| Deployment | Basic call | `temperature: 0` | JSON schema | Latency (basic / schema) |
|---|---|---|---|---|
| chat (gpt-6.1-terra) | ✅ 200 | ❌ 400, only default (1) allowed | ✅ 200 | 0.85s / 0.88s |
| chat-sol (gpt-6.1-sol) | ✅ 200 | ❌ 400 | ✅ 200 | 1.43s / 2.40s |
| chat-luna (gpt-6-luna) | ✅ 200 | ❌ 400 | ✅ 200 | 0.86s / 1.39s |
| chat-astra (gpt-6-astra) | ✅ 200 | ❌ 400 | ✅ 200 | 1.67s / 2.07s |
| chat-fallback (gpt-4.1) | ✅ 200 | ✅ 200 | ✅ 200 | 0.96s / 0.75s |
| embed (text-embedding-3-large) | ✅ vector returned | — | — | — |
| Container Registry | `acrffm_____` | ☐ |
| Container Apps env | `cae-ffm` | ☐ |
| Web app | `app-ffm-web` → URL: ______ | ☐ |
| Daily job | `job-ffm-refresh` | ☐ |

---

## 13. Lessons Log (add a row every time we learn something)

Anything surprising: errors, fixes, decisions, and especially **places where AI advice was wrong**. This feeds the GenAI Field Notes.

| Date / time | Who | What happened | What we learned / did | AI wrong? |
|---|---|---|---|---|
| 2026-11-07 | P2 | First `az role assignment list` returned nothing | Roles come through a **group**; need `--include-groups --include-inherited` to see them. We are Contributor | — |
| 2026-11-07 | P2 | First `az policy assignment list` returned nothing | Use `--disable-scope-strict-match` to include inherited policies. Still none found | — |
| 2026-11-07 | P2 | Found a pre-built `spoke` VNet + NAT gateway in East US | Organizer networking; deploy in `eastus`; don't use `its-snet` for Container Apps | — |
| 2026-11-07 | P2 | Portal "All resources" also shows a personal `Azure subscription 1` | Always check `az account show` before creating anything | — |
| 2026-11-07 | Lead | `list-models` in eastus offered gpt-4o-mini, gpt-4.1-mini, gpt-5-mini, gpt-5.4-mini, o3/o4-mini, text-embedding-3-small/large | Chose **gpt-4.1-mini** for chat: non-reasoning (fast, supports temperature 0), structured outputs, cheap. Reasoning models (o-series, gpt-5) are slower and restrict parameters like temperature | — |
| 2026-11-07 | Lead | Full `list-models` (no filter) showed many more models, up to gpt-6.1-terra (2026-10-26) | Team chose **gpt-6.1-terra** as `chat`, kept **gpt-4.1** as `chat-fallback`, upgraded embeddings to **text-embedding-3-large**. Terra is very new: test whether it accepts `temperature` and JSON-schema output before relying on it | — |
| 2026-11-07 | Lead | Smoke test: all GPT-6 deployments reject `temperature: 0` ("only the default (1) value is supported"); all support JSON-schema output; gpt-4.1 supports both | The LLM gateway must **not send `temperature`** to GPT-6 deployments. Without temperature 0, outputs vary run to run, so we rely on structured outputs + Verifier + caching cards, and we measure consistency in evaluation. The first smoke test only showed HTTP codes; content and finish_reason still need checking | — |
| 2026-11-07 | Lead | Content check: all 4 GPT-6 deployments return "pong", finish_reason "stop", 0 reasoning tokens; `reasoning_effort: "low"` accepted (200) | Gateway rules: GPT-6 → no `temperature`, send `reasoning_effort` + generous `max_completion_tokens`; gpt-4.1 → `temperature: 0`. Recheck reasoning-token use on real card prompts (a trivial prompt shows 0) | — |
| 2026-11-07 | Lead | New Cloud Shell session: `az ... keys list -g rg-ffm-team` → ResourceGroupNotFound | Each new session defaults to another subscription; run `az account set --subscription unc-sdis-solveathon-team01` first | — |
| 2026-11-07 | Lead | Azure OpenAI key1 was pasted into a chat by mistake | Treated as leaked: `az cognitiveservices account keys regenerate --key-name key1`, new key typed only into the git-ignored `.env` | — |
| 2026-11-07 | P2 | Ran the variable block twice; got SUFFIX 22772, then 10352 | Every run of `$RANDOM` makes a new suffix. Nothing was created with 22772, so we keep **10352**. After the first run, always hard-code the suffix | — |
| | | | | |
