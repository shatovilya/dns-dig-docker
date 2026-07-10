from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    READ_ONLY = "read-only"
    OPERATOR = "operator"
    ADMIN = "admin"

    def satisfies(self, required: "Role") -> bool:
        order = {Role.READ_ONLY: 0, Role.OPERATOR: 1, Role.ADMIN: 2}
        return order[self] >= order[required]


@dataclass(frozen=True)
class Principal:
    credential_id: str
    role: Role
