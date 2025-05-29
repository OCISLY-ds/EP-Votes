from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Vote(Base):
    __tablename__ = "votes"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    display_title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    reference = Column(String, nullable=True)
    geo_areas = Column(Text, nullable=True)
    position = Column(String, nullable=True)
    procedure = Column(Text, nullable=True)
    stats = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "display_title": self.display_title,
            "description": self.description,
            "reference": self.reference,
            "geo_areas": self.geo_areas,
            "position": self.position,
            "procedure": self.procedure,
            "stats": self.stats
        }

class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    faction = Column(String, nullable=True)
    country_code = Column(String, nullable=True)
    email = Column(String, nullable=True)
    facebook = Column(String, nullable=True)
    twitter = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    thumb_url = Column(String, nullable=True)