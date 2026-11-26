from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from src.models.base import Base

class DnDRules(Base):
    __tablename__ = "d_d_rules"

    id = Column(Integer, primary_key=True)
    rule_category = Column(String(100), nullable=False)
    rule_name = Column(String(255), nullable=False)
    rule_data = Column(JSONB)
    source = Column(String(255))
    version = Column(String(50))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<DnDRules {self.rule_category} - {self.rule_name}>"
