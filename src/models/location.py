from sqlalchemy import Column, Integer, String, BigInteger, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.sql import func
from src.models.base import Base

class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    location_type = Column(String(100))
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<Location {self.name} ({self.location_type})>"


class LocationConnection(Base):
    __tablename__ = "location_connections"

    id = Column(Integer, primary_key=True)
    from_location_id = Column(Integer, ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    to_location_id = Column(Integer, ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    description = Column(String(255))
    is_bidirectional = Column(Boolean, default=True)

    def __repr__(self):
        return f"<LocationConnection {self.from_location_id} -> {self.to_location_id}>"


class NPC(Base):
    __tablename__ = "npcs"

    id = Column(Integer, primary_key=True)
    location_id = Column(Integer, ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    personality = Column(Text)
    role = Column(String(100))
    is_hostile = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<NPC {self.name} ({self.role})>"
