"""Azure Blob Storage-client-helpers — poort van
`travel-experts-backend/integrations/azure_blob.py`.

Leest credentials/URL uit `env.ENV` i.p.v. rechtstreeks `os.getenv(...)`
(harde projectregel #8).
"""

from __future__ import annotations

import logging

from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient, ContainerClient


def get_credentials_from_env() -> ClientSecretCredential:
    from env import ENV

    return ClientSecretCredential(
        tenant_id=ENV.azure_tenant_id,
        client_id=ENV.azure_client_id,
        client_secret=ENV.azure_client_secret,
    )


def get_blob_service_client(disable_logging: bool = True) -> BlobServiceClient:
    from env import ENV

    credential = get_credentials_from_env()
    logger = logging.getLogger("logger_name")
    logger.disabled = disable_logging
    return BlobServiceClient(
        account_url=ENV.azure_storage_account_url, credential=credential, logger=logger
    )


def get_blob_container_client(
    blob_service_client: BlobServiceClient, container_name: str
) -> ContainerClient:
    return blob_service_client.get_container_client(container_name)


def get_blob_container_files(container_client: ContainerClient) -> list:
    return container_client.list_blobs()
