from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Vote(Base):
    __tablename__ = "votes"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String)
    display_title = Column(Text)
    description = Column(Text)
    reference = Column(String)
    geo_areas = Column(Text)
    position = Column(String)