# models.py
from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import json

Base = declarative_base()

class Vote(Base):
    __tablename__ = "votes"

    # Basis-Felder, wie bisher
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String, nullable=False)
    display_title = Column(Text, nullable=False)
    description = Column(Text)
    reference = Column(String)
    geo_areas = Column(Text)       # kommagetrennte Labels
    position = Column(String)

    # Neu: JSON-Blob mit dem kompletten Roh-Datensatz
    raw_json = Column(Text, nullable=False)

    # Beziehungen zu Statistiken und Member-Votes
    stats = relationship("Stats", back_populates="vote", cascade="all, delete-orphan")
    member_votes = relationship("MemberVote", back_populates="vote", cascade="all, delete-orphan")


class Stats(Base):
    __tablename__ = "stats"
    id = Column(Integer, primary_key=True, index=True)

    vote_id = Column(Integer, ForeignKey("votes.id", ondelete="CASCADE"))
    vote = relationship("Vote", back_populates="stats")

    # Gesamt-Anzahlen
    total_for = Column(Integer, nullable=False)
    total_against = Column(Integer, nullable=False)
    total_abstention = Column(Integer, nullable=False)
    total_did_not_vote = Column(Integer, nullable=False)

    # Beziehungen zu ByGroup und ByCountry
    by_groups = relationship("ByGroup", back_populates="stats", cascade="all, delete-orphan")
    by_countries = relationship("ByCountry", back_populates="stats", cascade="all, delete-orphan")


class ByGroup(Base):
    __tablename__ = "stats_by_group"
    id = Column(Integer, primary_key=True, index=True)

    stats_id = Column(Integer, ForeignKey("stats.id", ondelete="CASCADE"))
    stats = relationship("Stats", back_populates="by_groups")

    # Angaben zur Fraktion
    group_code = Column(String)        # z. B. "EPP"
    group_label = Column(String)       # z. B. "European People’s Party"
    group_short_label = Column(String) # z. B. "EPP"

    # Zahlen
    for_count = Column(Integer, nullable=False)
    against_count = Column(Integer, nullable=False)
    abstention_count = Column(Integer, nullable=False)
    did_not_vote_count = Column(Integer, nullable=False)


class ByCountry(Base):
    __tablename__ = "stats_by_country"
    id = Column(Integer, primary_key=True, index=True)

    stats_id = Column(Integer, ForeignKey("stats.id", ondelete="CASCADE"))
    stats = relationship("Stats", back_populates="by_countries")

    # Angaben zum Land
    country_code = Column(String)       # z. B. "DEU"
    country_iso_alpha_2 = Column(String) # z. B. "DE"
    country_label = Column(String)      # z. B. "Germany"

    # Zahlen
    for_count = Column(Integer, nullable=False)
    against_count = Column(Integer, nullable=False)
    abstention_count = Column(Integer, nullable=False)
    did_not_vote_count = Column(Integer, nullable=False)


class MemberVote(Base):
    __tablename__ = "member_votes"
    id = Column(Integer, primary_key=True, index=True)

    vote_id = Column(Integer, ForeignKey("votes.id", ondelete="CASCADE"))
    vote = relationship("Vote", back_populates="member_votes")

    # Informationen zum Mitglied (eine flache Kopie der JSON-Struktur)
    member_id = Column(Integer, nullable=False)
    first_name = Column(String)
    last_name = Column(String)
    date_of_birth = Column(String)
    country_code = Column(String)
    country_iso_alpha_2 = Column(String)
    country_label = Column(String)
    group_code = Column(String)
    group_label = Column(String)
    group_short_label = Column(String)
    photo_url = Column(String)
    thumb_url = Column(String)
    email = Column(String)
    facebook = Column(String)
    twitter = Column(String)

    position = Column(String)  # z. B. "FOR", "AGAINST", etc.