"""Email account CRUD."""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from src.backend.deps import get_account_store
from src.backend.schemas import AccountCreate, AccountOut


class PreviewRequest(BaseModel):
    account_id: str
    folder: str = "INBOX"


class PreviewResponse(BaseModel):
    folder: str
    total: int

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(store=Depends(get_account_store)):
    try:
        accounts = store.list()
        default_id = store.default_id()
    finally:
        store.close()

    return [
        AccountOut(
            id=a["id"],
            label=a.get("label") or a.get("email_user", ""),
            imap_server=a.get("imap_server", ""),
            imap_port=a.get("imap_port", 993),
            email_user=a.get("email_user", ""),
            provider=a.get("provider", ""),
            is_default=(a["id"] == default_id),
        )
        for a in accounts
    ]


@router.post("/{account_id}/default", status_code=200)
def set_default_account(account_id: str, store=Depends(get_account_store)):
    """把该账号设为默认（无 X-Account-Id 时 / CLI 使用的账号）。"""
    try:
        if not store.get(account_id):
            raise HTTPException(status_code=404, detail="Account not found")
        store.set_default(account_id)
    finally:
        store.close()
    return {"default_account_id": account_id}


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: str, store=Depends(get_account_store)):
    try:
        acct = store.get(account_id)
    finally:
        store.close()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountOut(
        id=acct["id"],
        label=acct.get("label") or acct.get("email_user", ""),
        imap_server=acct.get("imap_server", ""),
        imap_port=acct.get("imap_port", 993),
        email_user=acct.get("email_user", ""),
        provider=acct.get("provider", ""),
    )


@router.post("", response_model=AccountOut, status_code=201)
def create_account(body: AccountCreate, store=Depends(get_account_store)):
    try:
        acct = store.add(
            label=body.label,
            imap_server=body.imap_server,
            imap_port=body.imap_port,
            email_user=body.email_user,
            email_pass=body.email_pass,
            provider=body.provider,
        )
    finally:
        store.close()
    return AccountOut(
        id=acct["id"],
        label=acct.get("label") or acct.get("email_user", ""),
        imap_server=acct.get("imap_server", ""),
        imap_port=acct.get("imap_port", 993),
        email_user=acct.get("email_user", ""),
        provider=acct.get("provider", ""),
    )


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: str, store=Depends(get_account_store)):
    try:
        store.delete(account_id)
    finally:
        store.close()


@router.post("/migrate-from-env", status_code=200)
def migrate_from_env(store=Depends(get_account_store)):
    try:
        store.ensure_default_from_env()
        accounts = store.list()
    finally:
        store.close()
    return {"migrated": len(accounts) > 0, "account_count": len(accounts)}


@router.post("/preview", response_model=PreviewResponse)
def preview_folder(request: PreviewRequest, store=Depends(get_account_store)):
    """快速获取 IMAP 文件夹邮件总数（不下载任何内容）。"""
    from src.backend.mail.imap_client import IMAPClient

    try:
        account = store.get(request.account_id)
    finally:
        store.close()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    with IMAPClient(account) as client:
        total = client.get_folder_count(request.folder)
    return PreviewResponse(folder=request.folder, total=total)
