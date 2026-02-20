from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from lfx.services.deployment.exceptions import (
    DeploymentConflictError,
    DeploymentError,
    DeploymentNotFoundError,
    InvalidContentError,
    InvalidDeploymentTypeError,
)
from lfx.services.deployment.schema import (
    ArtifactType,
    BaseConfigData,
    ConfigItemResult,
    ConfigListResult,
    ConfigResult,
    ConfigUpdate,
    DeploymentAccountId,
    DeploymentCreate,
    DeploymentCreateResult,
    DeploymentItem,
    DeploymentList,
    DeploymentRedeploymentResult,
    DeploymentStatusResult,
    DeploymentType,
    DeploymentUpdate,
    DeploymentUpdateResult,
    SnapshotGetResult,
    SnapshotItemsCreate,
    SnapshotListResult,
    SnapshotResult,
)
from lfx.services.deployment_router.exceptions import (
    DeploymentAccountNotFoundError,
    DeploymentRouterError,
)
from lfx.services.deps import get_deployment_router_service
from lfx.services.interfaces import DeploymentRouterServiceProtocol, DeploymentServiceProtocol
from pydantic import BaseModel, Field, field_validator, model_validator

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.services.database.models.deployment_provider_account.crud import (
    create_provider_account_for_user,
    delete_provider_account_for_user,
    get_provider_account_by_id_for_user,
    list_provider_accounts_for_user,
    update_provider_account_for_user,
)
from langflow.services.database.models.deployment_provider_account.model import DeploymentProviderAccount

router = APIRouter(prefix="/deployment-providers/{account_id}", tags=["Deployments"])
provider_accounts_router = APIRouter(prefix="/deployment-providers", tags=["Deployment Providers"])


class DeploymentCreateRequest(DeploymentCreate):
    """Create deployment request."""


class DeploymentUpdateRequest(DeploymentUpdate):
    """Update deployment request."""


class DeploymentTypesResponse(BaseModel):
    """List deployment types response."""

    deployment_types: list[DeploymentType]


class DeploymentConfigCreateRequest(BaseConfigData):
    """Create deployment config request."""


class DeploymentConfigUpdateRequest(ConfigUpdate):
    """Update deployment config request."""


class SnapshotCreateRequest(SnapshotItemsCreate):
    """Create snapshot request."""


class DeploymentDuplicateRequest(BaseModel):
    """Create deployment duplicate request."""

    deployment_type: DeploymentType


class DeploymentProviderAccountCreateRequest(BaseModel):
    account_id: str = Field(min_length=1, description="Provider tenant/organization identifier.")
    provider_key: str = Field(min_length=1, description="Deployment adapter routing key.")
    backend_url: str = Field(min_length=1, description="Deployment provider backend URL.")
    api_key: str = Field(min_length=1, description="Deployment provider API key.")

    @field_validator("account_id", "provider_key", "backend_url", "api_key")
    @classmethod
    def normalize_required_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "Field must not be empty or whitespace."
            raise ValueError(msg)
        return normalized


class DeploymentProviderAccountUpdateRequest(BaseModel):
    account_id: str | None = Field(default=None, min_length=1, description="Provider tenant/organization identifier.")
    provider_key: str | None = Field(default=None, min_length=1, description="Deployment adapter routing key.")
    backend_url: str | None = Field(default=None, min_length=1, description="Deployment provider backend URL.")
    api_key: str | None = Field(default=None, min_length=1, description="Deployment provider API key.")

    @field_validator("account_id", "provider_key", "backend_url", "api_key")
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            msg = "Field must not be empty or whitespace."
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def ensure_any_field_provided(self) -> DeploymentProviderAccountUpdateRequest:
        if all(value is None for value in (self.account_id, self.provider_key, self.backend_url, self.api_key)):
            msg = "At least one field must be provided for update."
            raise ValueError(msg)
        return self


class DeploymentProviderAccountResponse(BaseModel):
    id: UUID
    account_id: str
    provider_key: str
    backend_url: str
    registered_at: datetime | None
    updated_at: datetime | None
    has_api_key: bool


class DeploymentProviderAccountListResponse(BaseModel):
    deployment_providers: list[DeploymentProviderAccountResponse]


def _to_provider_account_response(provider_account: DeploymentProviderAccount) -> DeploymentProviderAccountResponse:
    return DeploymentProviderAccountResponse(
        id=provider_account.id,
        account_id=provider_account.account_id,
        provider_key=provider_account.provider_key,
        backend_url=provider_account.backend_url,
        registered_at=provider_account.registered_at,
        updated_at=provider_account.updated_at,
        has_api_key=bool(provider_account.api_key),
    )


@provider_accounts_router.post(
    "/",
    response_model=DeploymentProviderAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_account(
    payload: DeploymentProviderAccountCreateRequest,
    user: CurrentActiveUser,
    db: DbSession,
):
    provider_account = await create_provider_account_for_user(
        db,
        user_id=user.id,
        account_id=payload.account_id,
        provider_key=payload.provider_key,
        backend_url=payload.backend_url,
        api_key=payload.api_key,
    )
    return _to_provider_account_response(provider_account)


@provider_accounts_router.get("/", response_model=DeploymentProviderAccountListResponse)
async def list_provider_accounts(
    user: CurrentActiveUser,
    db: DbSession,
):
    provider_accounts = await list_provider_accounts_for_user(db, user_id=user.id)
    return DeploymentProviderAccountListResponse(
        deployment_providers=[_to_provider_account_response(item) for item in provider_accounts]
    )


@provider_accounts_router.get("/{account_id}", response_model=DeploymentProviderAccountResponse)
async def get_provider_account(
    account_id: DeploymentAccountId,
    user: CurrentActiveUser,
    db: DbSession,
):
    provider_account = await get_provider_account_by_id_for_user(db, account_id=account_id, user_id=user.id)
    if provider_account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment provider account not found.")
    return _to_provider_account_response(provider_account)


@provider_accounts_router.patch("/{account_id}", response_model=DeploymentProviderAccountResponse)
async def update_provider_account(
    account_id: DeploymentAccountId,
    payload: DeploymentProviderAccountUpdateRequest,
    user: CurrentActiveUser,
    db: DbSession,
):
    provider_account = await get_provider_account_by_id_for_user(db, account_id=account_id, user_id=user.id)
    if provider_account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment provider account not found.")
    updated_provider_account = await update_provider_account_for_user(
        db,
        provider_account=provider_account,
        account_id=payload.account_id,
        provider_key=payload.provider_key,
        backend_url=payload.backend_url,
        api_key=payload.api_key,
    )
    return _to_provider_account_response(updated_provider_account)


@provider_accounts_router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_account(
    account_id: DeploymentAccountId,
    user: CurrentActiveUser,
    db: DbSession,
):
    provider_account = await get_provider_account_by_id_for_user(db, account_id=account_id, user_id=user.id)
    if provider_account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment provider account not found.")
    await delete_provider_account_for_user(db, provider_account=provider_account)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_deployment_router_service() -> DeploymentRouterServiceProtocol:
    deployment_router_service = get_deployment_router_service()
    if deployment_router_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Deployment router service is not available.",
        )
    return deployment_router_service


async def _resolve_deployment_adapter(
    account_id: DeploymentAccountId,
    *,
    user_id,
    db,
) -> DeploymentServiceProtocol:
    deployment_router_service = _require_deployment_router_service()
    try:
        return await deployment_router_service.resolve_adapter(account_id=account_id, user_id=user_id, db=db)
    except DeploymentAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentRouterError as exc:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        raise HTTPException(status_code=status_code, detail=exc.message) from exc


def _raise_http_for_value_error(exc: ValueError) -> None:
    status_code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_400_BAD_REQUEST
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/deployments", response_model=DeploymentCreateResult, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    account_id: DeploymentAccountId,
    user: CurrentActiveUser,
    payload: DeploymentCreateRequest,
    db: DbSession,
):
    """Create a deployment using the provider routing ID."""
    # print(f"Deploying deployment with account ID: {account_id}")
    # print(f"Payload: {payload}")
    # print(f"User: {user}")
    # print(f"DB: {db}")
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        result = await deployment_adapter.create_deployment(
            user_id=user.id,
            deployment=payload,
            db=db,
        )
    except DeploymentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except InvalidContentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return DeploymentCreateResult(**result.model_dump(exclude_unset=True))


@router.get("/deployment-types", response_model=DeploymentTypesResponse)
async def list_deployment_types(
    account_id: DeploymentAccountId,
    user: CurrentActiveUser,
    db: DbSession,
):
    """List deployment types for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        deployment_types = await deployment_adapter.list_deployment_types(
            user_id=user.id,
            db=db,
        )
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DeploymentTypesResponse(deployment_types=deployment_types)


@router.get("/deployments", response_model=DeploymentList)
async def list_deployments(
    account_id: DeploymentAccountId,
    user: CurrentActiveUser,
    db: DbSession,
    deployment_type: DeploymentType | None = None,
):
    """List deployments for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        deployments = await deployment_adapter.list_deployments(
            user_id=user.id,
            deployment_type=deployment_type,
            db=db,
        )
    except InvalidDeploymentTypeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DeploymentList(**deployments.model_dump(exclude_unset=True))


@router.get("/deployments/{deployment_id}", response_model=DeploymentItem)
async def get_deployment(
    account_id: DeploymentAccountId,
    deployment_id: str,
    user: CurrentActiveUser,
    db: DbSession,
):
    """Get a deployment for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        deployment = await deployment_adapter.get_deployment(
            user_id=user.id,
            deployment_id=deployment_id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DeploymentItem(**deployment.model_dump(exclude_unset=True))


@router.patch(
    "/deployments/{deployment_id}",
    response_model=DeploymentUpdateResult,
)
async def update_deployment(
    account_id: DeploymentAccountId,
    deployment_id: str,
    payload: DeploymentUpdateRequest,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Update a deployment for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        update_result = await deployment_adapter.update_deployment(
            deployment_id=deployment_id,
            update_data=payload,
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DeploymentUpdateResult(**update_result.model_dump(exclude_unset=True))


@router.delete("/deployments/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    account_id: DeploymentAccountId,
    deployment_id: str,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Delete a deployment for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        await deployment_adapter.delete_deployment(
            deployment_id=deployment_id,
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/deployments/{deployment_id}/redeploy",
    response_model=DeploymentRedeploymentResult,
    status_code=status.HTTP_201_CREATED,
)
async def redeploy_deployment(
    account_id: DeploymentAccountId,
    deployment_id: str,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Redeploy a deployment for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        redeploy_result = await deployment_adapter.redeploy_deployment(
            deployment_id=deployment_id,
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DeploymentRedeploymentResult(**redeploy_result.model_dump(exclude_unset=True))


@router.post(
    "/deployments/{deployment_id}/duplicate",
    response_model=DeploymentItem,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_deployment(
    account_id: DeploymentAccountId,
    deployment_id: str,
    payload: DeploymentDuplicateRequest,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Duplicate a deployment for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        clone_result = await deployment_adapter.duplicate_deployment(
            deployment_id=deployment_id,
            deployment_type=payload.deployment_type,
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DeploymentItem(**clone_result.model_dump(exclude_unset=True))


@router.get(
    "/deployments/{deployment_id}/status",
    response_model=DeploymentStatusResult,
)
async def get_deployment_status(
    account_id: DeploymentAccountId,
    deployment_id: str,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Get deployment health for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        health_result = await deployment_adapter.get_deployment_status(
            deployment_id=deployment_id,
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return DeploymentStatusResult(**health_result.model_dump(exclude_unset=True))


@router.get("/snapshots", response_model=SnapshotListResult)
async def list_snapshots(
    account_id: DeploymentAccountId,
    user: CurrentActiveUser,
    db: DbSession,
    artifact_type: ArtifactType | None = None,
):
    """List snapshots for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        snapshot_list_result = await deployment_adapter.list_snapshots(
            user_id=user.id,
            artifact_type=artifact_type,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return SnapshotListResult(**snapshot_list_result.model_dump(exclude_unset=True))


@router.post("/snapshots", response_model=SnapshotResult, status_code=status.HTTP_201_CREATED)
async def create_snapshots(
    account_id: DeploymentAccountId,
    payload: SnapshotCreateRequest,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Create snapshots for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        create_result = await deployment_adapter.create_snapshots(
            user_id=user.id,
            snapshot_items=payload,
            db=db,
        )
    except InvalidContentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message) from exc
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return SnapshotResult(**create_result.model_dump(exclude_unset=True))


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotGetResult)
async def get_snapshot(
    account_id: DeploymentAccountId,
    snapshot_id: str,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Get snapshot for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        snapshot = await deployment_adapter.get_snapshot(
            user_id=user.id,
            snapshot_id=snapshot_id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return SnapshotGetResult(**snapshot.model_dump(exclude_unset=True))


@router.delete("/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snapshot(
    account_id: DeploymentAccountId,
    snapshot_id: str,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Delete snapshot for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        await deployment_adapter.delete_snapshot(
            user_id=user.id,
            snapshot_id=snapshot_id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/configs", response_model=ConfigResult, status_code=status.HTTP_201_CREATED)
async def create_deployment_config(
    account_id: DeploymentAccountId,
    payload: DeploymentConfigCreateRequest,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Create deployment config for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        create_result = await deployment_adapter.create_deployment_config(
            config=payload,
            user_id=user.id,
            db=db,
        )
    except DeploymentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except InvalidContentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message) from exc
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ConfigResult(**create_result.model_dump(exclude_unset=True))


@router.get("/configs", response_model=ConfigListResult)
async def list_deployment_configs(
    account_id: DeploymentAccountId,
    db: DbSession,
    user: CurrentActiveUser,
):
    """List deployment configs for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        configs_result = await deployment_adapter.list_deployment_configs(
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ConfigListResult(**configs_result.model_dump(exclude_unset=True))


@router.get("/configs/{config_id}", response_model=ConfigItemResult)
async def get_deployment_config(
    account_id: DeploymentAccountId,
    config_id: str,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Get deployment config for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        config = await deployment_adapter.get_deployment_config(
            config_id=config_id,
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ConfigItemResult(**config.model_dump(exclude_unset=True))


@router.patch("/configs/{config_id}", response_model=ConfigResult)
async def update_deployment_config(
    account_id: DeploymentAccountId,
    config_id: str,
    payload: DeploymentConfigUpdateRequest,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Update deployment config for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        update_result = await deployment_adapter.update_deployment_config(
            config_id=config_id,
            update_data=payload,
            user_id=user.id,
            db=db,
        )
    except InvalidContentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message) from exc
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ConfigResult(**update_result.model_dump(exclude_unset=True))


@router.delete("/configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment_config(
    account_id: DeploymentAccountId,
    config_id: str,
    db: DbSession,
    user: CurrentActiveUser,
):
    """Delete deployment config for a provider routing ID."""
    deployment_adapter = await _resolve_deployment_adapter(account_id, user_id=user.id, db=db)
    try:
        await deployment_adapter.delete_deployment_config(
            config_id=config_id,
            user_id=user.id,
            db=db,
        )
    except ValueError as exc:
        _raise_http_for_value_error(exc)
    except DeploymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except DeploymentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


deployment_router = APIRouter()
deployment_router.include_router(provider_accounts_router)
deployment_router.include_router(router)
router = deployment_router
