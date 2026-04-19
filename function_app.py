import azure.functions as func
import os
import requests
import logging
import traceback
import json
from azure.identity import ManagedIdentityCredential
from azure.mgmt.containerinstance import ContainerInstanceManagementClient
from azure.mgmt.containerinstance.models import (
    ContainerGroup, Container,
    ResourceRequirements, ResourceRequests,
    ImageRegistryCredential, OperatingSystemTypes,
    ContainerGroupRestartPolicy, EnvironmentVariable
)

app = func.FunctionApp()

def get_aci_client():
    credential = ManagedIdentityCredential()
    subscription_id = os.environ["SUBSCRIPTION_ID"]
    return ContainerInstanceManagementClient(credential, subscription_id)

def get_aci_info():
    aci_name = os.environ["ACI_NAME"]
    aci_rg   = os.environ["ACI_RG"]
    return aci_name, aci_rg


@app.route(route="start-build", methods=["POST"])
def start_build(req: func.HttpRequest) -> func.HttpResponse:
    try:
        req_body = req.get_json()
        github_repo = req_body.get("github_repo")
        if not github_repo:
            return func.HttpResponse("github_repo is required", status_code=400)

        github_pat = os.environ["GITHUB_PAT"]

        token_res = requests.post(
            f"https://api.github.com/repos/{github_repo}/actions/runners/registration-token",
            headers={
                "Authorization": f"token {github_pat}",
                "Accept": "application/vnd.github+json"
            }
        )

        logging.info(f"Token response status: {token_res.status_code}")

        if token_res.status_code != 201:
            return func.HttpResponse(f"Failed to get runner token: {token_res.text}", status_code=500)

        runner_token = token_res.json()["token"]

        acr_server = os.environ["ACR_SERVER"]
        acr_user   = os.environ["ACR_USER"]
        acr_pass   = os.environ["ACR_PASS"]
        aci_name, aci_rg = get_aci_info()
        aci_image  = os.environ["ACI_IMAGE"]

        client = get_aci_client()

        try:
            client.container_groups.begin_delete(aci_rg, aci_name).result()
            logging.info("Existing ACI deleted")
        except Exception:
            logging.info("No existing ACI to delete")

        container_group = ContainerGroup(
            location="japaneast",
            containers=[
                Container(
                    name=aci_name,
                    image=aci_image,
                    resources=ResourceRequirements(
                        requests=ResourceRequests(cpu=４.0, memory_in_gb=８.0)
                    ),
                    environment_variables=[
                        EnvironmentVariable(name="GITHUB_REPO_URL", value=f"https://github.com/{github_repo}"),
                        EnvironmentVariable(name="RUNNER_TOKEN", secure_value=runner_token)
                    ]
                )
            ],
            image_registry_credentials=[
                ImageRegistryCredential(
                    server=acr_server,
                    username=acr_user,
                    password=acr_pass
                )
            ],
            os_type=OperatingSystemTypes.LINUX,
            restart_policy=ContainerGroupRestartPolicy.NEVER
        )

        client.container_groups.begin_create_or_update(aci_rg, aci_name, container_group).result()
        logging.info("ACI started successfully")

        return func.HttpResponse("ACI started successfully", status_code=200)

    except Exception as e:
        logging.error(f"Exception: {traceback.format_exc()}")
        return func.HttpResponse(f"Exception: {str(e)}", status_code=500)


@app.route(route="aci-status", methods=["GET"])
def aci_status(req: func.HttpRequest) -> func.HttpResponse:
    try:
        aci_name, aci_rg = get_aci_info()
        client = get_aci_client()

        try:
            group = client.container_groups.get(aci_rg, aci_name)
            container = group.containers[0]
            state = container.instance_view.current_state if container.instance_view else None
            logs = client.containers.list_logs(aci_rg, aci_name, aci_name)
            result = {
                "status": state.state if state else "Unknown",
                "exit_code": state.exit_code if state else None,
                "logs": logs.content if logs.content else ""
            }
        except Exception as not_found:
            if "ResourceNotFound" in str(not_found):
                result = {
                    "status": "NotFound",
                    "exit_code": None,
                    "logs": "ACI is not running."
                }
            else:
                raise

        return func.HttpResponse(json.dumps(result), mimetype="application/json", status_code=200)

    except Exception as e:
        logging.error(f"Exception: {traceback.format_exc()}")
        return func.HttpResponse(f"Exception: {str(e)}", status_code=500)


@app.route(route="stop-build", methods=["POST"])
def stop_build(req: func.HttpRequest) -> func.HttpResponse:
    try:
        aci_name, aci_rg = get_aci_info()
        client = get_aci_client()

        client.container_groups.begin_delete(aci_rg, aci_name).result()
        logging.info("ACI deleted successfully")

        return func.HttpResponse("ACI deleted successfully", status_code=200)

    except Exception as e:
        logging.error(f"Exception: {traceback.format_exc()}")
        return func.HttpResponse(f"Exception: {str(e)}", status_code=500)


@app.route(route="upload-and-build", methods=["POST"])
def upload_and_build(req: func.HttpRequest) -> func.HttpResponse:
    try:
        from azure.storage.blob import BlobServiceClient, generate_container_sas, ContainerSasPermissions
        from azure.mgmt.containerregistry import ContainerRegistryManagementClient
        from azure.mgmt.containerregistry.models import (
            DockerBuildRequest, PlatformProperties, OS, AgentProperties
        )
        from datetime import datetime, timedelta, timezone

        storage_conn    = os.environ["BUILD_STORAGE_CONNECTION"]
        storage_account = os.environ["BUILD_STORAGE_ACCOUNT"]
        storage_key     = os.environ["BUILD_STORAGE_KEY"]
        container_name  = "dockerfile-context"
        subscription_id = os.environ["SUBSCRIPTION_ID"]
        acr_server      = os.environ["ACR_SERVER"]
        acr_name        = acr_server.split(".")[0]
        aci_rg          = os.environ["ACI_RG"]

        blob_service     = BlobServiceClient.from_connection_string(storage_conn)
        container_client = blob_service.get_container_client(container_name)

        try:
            container_client.create_container()
        except Exception:
            pass

        files    = req.files
        uploaded = []

        for filename in ["Dockerfile", "entrypoint.sh"]:
            if filename in files:
                file_data   = files[filename].read()
                blob_client = blob_service.get_blob_client(
                    container=container_name,
                    blob=filename
                )
                blob_client.upload_blob(file_data, overwrite=True)
                uploaded.append(filename)
                logging.info(f"Uploaded: {filename}")

        if not uploaded:
            return func.HttpResponse("No files uploaded", status_code=400)

        sas_token = generate_container_sas(
            account_name=storage_account,
            container_name=container_name,
            account_key=storage_key,
            permission=ContainerSasPermissions(read=True, list=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=1)
        )

        context_url = f"https://{storage_account}.blob.core.windows.net/{container_name}?{sas_token}"

        credential = ManagedIdentityCredential()
        acr_client = ContainerRegistryManagementClient(credential, subscription_id)

        build_request = DockerBuildRequest(
            image_names=["flutter-builder:latest"],
            is_push_enabled=True,
            source_location=context_url,
            platform=PlatformProperties(os=OS.LINUX),
            docker_file_path="Dockerfile",
            agent_configuration=AgentProperties(cpu=2)
        )

        run = acr_client.registries.begin_schedule_run(
            aci_rg, acr_name, build_request
        ).result()

        logging.info(f"ACR build started: {run.run_id}")
        return func.HttpResponse(
            json.dumps({"uploaded": uploaded, "run_id": run.run_id}),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logging.error(f"Exception: {traceback.format_exc()}")
        return func.HttpResponse(f"Exception: {str(e)}", status_code=500)
