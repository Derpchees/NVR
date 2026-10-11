"""Request bodies accepted by the API."""

from __future__ import annotations

import ipaddress

from pydantic import BaseModel, Field, field_validator


class CameraInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str
    port: int = Field(default=80, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre es obligatorio")
        return value

    @field_validator("host")
    @classmethod
    def private_ip_only(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError("Introduce la dirección IP local de la cámara") from exc
        if not address.is_private or address.is_loopback or address.is_multicast:
            raise ValueError("Solo se permiten direcciones IP privadas")
        return str(address)


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CredentialsInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    new_password: str | None = Field(default=None, min_length=8, max_length=256)


class PTZMoveInput(BaseModel):
    pan: float = Field(default=0, ge=-1, le=1)
    tilt: float = Field(default=0, ge=-1, le=1)
    zoom: float = Field(default=0, ge=-1, le=1)
    duration_ms: int = Field(default=650, ge=100, le=3000)


class IRCutInput(BaseModel):
    mode: str = Field(pattern="^(ON|OFF|AUTO)$")


class RelayInput(BaseModel):
    active: bool


class AuxCommandInput(BaseModel):
    command: str = Field(min_length=1, max_length=128)


class PlayerEventInput(BaseModel):
    event: str = Field(min_length=1, max_length=48)
    detail: str = Field(default="", max_length=120)
    fatal: bool = False
    media_time: float | None = Field(default=None, ge=0, le=31_536_000)
    latency: float | None = Field(default=None, ge=0, le=3_600)
