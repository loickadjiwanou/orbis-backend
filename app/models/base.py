"""Utilitaires Pydantic v2 pour MongoDB (ObjectId, sérialisation)."""
from typing import Any
from bson import ObjectId
from pydantic import BaseModel, ConfigDict
from pydantic_core import core_schema


class PyObjectId(str):
    """Type Pydantic v2 compatible avec les ObjectId BSON de MongoDB."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: Any, _handler: Any
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_wrap_validator_function(
            cls._validate,
            core_schema.union_schema(
                [core_schema.str_schema(), core_schema.is_instance_schema(ObjectId)]
            ),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def _validate(cls, value: Any, handler: Any) -> "PyObjectId":
        if isinstance(value, ObjectId):
            return cls(str(value))
        validated = handler(value)
        if not ObjectId.is_valid(validated):
            raise ValueError(f"ObjectId invalide : {validated!r}")
        return cls(validated)


class OrbisBaseModel(BaseModel):
    """BaseModel commun avec configuration MongoDB."""

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
    )
