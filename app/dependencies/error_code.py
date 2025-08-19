from enum import Enum


class ErrorCode(str, Enum):
    PASSWORD_MUST_BE_AT_LEAST_6_CHARACTERS = "PASSWORD_MUST_BE_AT_LEAST_6_CHARACTERS"