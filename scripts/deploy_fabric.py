"""
deploy_fabric.py

Triggers a Fabric Deployment Pipeline to promote content from a source stage
(e.g. staging) to the target stage (e.g. production), then polls until the
operation completes or fails.

Authentication:
    Uses the fabric-gh-runner VM's system-assigned managed identity via
    DefaultAzureCredential — no secrets required.

Environment variables (set as GitHub Actions variables in repo settings):
    DEPLOYMENT_PIPELINE_ID   GUID of the Fabric Deployment Pipeline
    SOURCE_STAGE_ORDER       Integer stage index to promote from (e.g. 1 for staging)
    TARGET_STAGE_ORDER       Integer stage index to promote to (e.g. 2 for production)

Exit codes:
    0  Deploy succeeded
    1  Deploy failed or timed out
"""

import os
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"

PIPELINE_ID = os.environ["DEPLOYMENT_PIPELINE_ID"]
SOURCE_STAGE = int(os.environ["SOURCE_STAGE_ORDER"])
TARGET_STAGE = int(os.environ["TARGET_STAGE_ORDER"])

POLL_INTERVAL_SECONDS = 15
TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def get_headers() -> dict[str, str]:
    credential = DefaultAzureCredential()
    token = credential.get_token("https://api.fabric.microsoft.com/.default")
    return {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
    }


def trigger_deploy(headers: dict) -> str:
    """
    POST to the Fabric Deployment Pipeline deploy endpoint.
    Returns the operation ID from the Location header for polling.
    """
    url = f"{FABRIC_API}/deploymentPipelines/{PIPELINE_ID}/deploy"
    payload = {
        "sourceStageOrder": SOURCE_STAGE,
        "targetStageOrder": TARGET_STAGE,
        "isBackwardDeployment": False,
        "newWorkspace": None,
        "options": {
            "allowCreateArtifact": False,  # do not create new items in prod — only update existing
            "allowOverwriteArtifact": True,
            "allowOverwriteTargetWorkspaceDB": False,
            "allowPurgeData": False,
            "allowSkipItemsWithSSBIContent": False,
        },
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if resp.status_code == 202:
        location = resp.headers.get("Location") or resp.headers.get("location", "")
        if not location:
            print("Error: 202 accepted but no Location header returned.")
            sys.exit(1)
        print(f"Deploy triggered. Polling: {location}")
        return location

    print(f"Deploy request failed: {resp.status_code}\n{resp.text}")
    sys.exit(1)


def poll_operation(location: str, headers: dict) -> bool:
    """
    Poll the long-running operation until it reaches a terminal state.
    Returns True on success, False on failure.
    """
    elapsed = 0
    while elapsed < TIMEOUT_SECONDS:
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

        resp = requests.get(location, headers=headers, timeout=30)
        if resp.status_code == 200:
            body = resp.json()
            status = body.get("status", "").lower()
            print(f"  [{elapsed:>4}s] Status: {status}")

            if status == "succeeded":
                return True
            if status in ("failed", "cancelled"):
                error = body.get("error", {})
                print(f"\nDeploy failed: {error.get('errorCode')} — {error.get('message')}")
                return False
        else:
            print(f"  [{elapsed:>4}s] Poll returned {resp.status_code}, retrying...")

    print(f"\nDeploy timed out after {TIMEOUT_SECONDS // 60} minutes.")
    return False


def main() -> int:
    print(f"Deploying pipeline '{PIPELINE_ID}': stage {SOURCE_STAGE} → stage {TARGET_STAGE}")
    headers = get_headers()

    location = trigger_deploy(headers)
    succeeded = poll_operation(location, headers)

    if succeeded:
        print("\nDeploy succeeded.")
        return 0
    else:
        print("\nDeploy failed. Check the Fabric Deployment Pipeline for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
