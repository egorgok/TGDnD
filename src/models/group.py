from sqlalchemy import Column, Integer, String, BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from src.models.base import Base

class Group(Base):
    __tablename__ = "groups"

    id = Column(BigInteger, primary_key=True)
    title = Column(String(255), nullable=True)
    group_type = Column(String(50), nullable=True)
    gm_prompt = Column(Text, nullable=True)
    world_description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Group {self.id} - {self.title}>"


class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(50), default="player")
    joined_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<GroupMember {self.user_id} in {self.group_id} as {self.role}>"
