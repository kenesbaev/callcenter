from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from teamora_api.enums import RoleName


class RegisterRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=160)
    company_slug: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, value: str) -> str:
        if not any(ch.isupper() for ch in value) or not any(ch.islower() for ch in value):
            raise ValueError("Password must contain uppercase and lowercase letters")
        if not any(ch.isdigit() for ch in value):
            raise ValueError("Password must contain a number")
        return value


class LoginRequest(BaseModel):
    company_slug: str = Field(min_length=3, max_length=80)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AuthUser(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str
    role: RoleName


class AuthTenant(BaseModel):
    id: UUID
    name: str
    slug: str


class AuthResponse(BaseModel):
    user: AuthUser
    tenant: AuthTenant
    csrf_token: str
