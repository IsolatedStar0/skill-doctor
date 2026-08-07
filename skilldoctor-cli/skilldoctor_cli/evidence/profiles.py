from __future__ import annotations

from .contract import SkillProfile


DEFAULT_PROFILE = SkillProfile(skill_id="default", min_evidence_score=0.75)

PUCK_RULE_RCA_PROFILE = SkillProfile(
    skill_id="puck-rule-rca",
    required_checks=(
        "history_regular",
        "today_vs_day1_similar",
        "today_vs_day2_similar",
    ),
    critical_checks=(
        "today_vs_day1_similar",
        "today_vs_day2_similar",
    ),
    min_evidence_score=0.75,
    high_confidence_requires_artifact_types=("chart", "timeseries"),
)

RELEASE_CHECKLIST_PROFILE = SkillProfile(
    skill_id="release-checklist",
    required_checks=("rollback-gate-present",),
    critical_checks=("rollback-gate-present",),
    min_evidence_score=0.75,
)


PROFILES = {
    PUCK_RULE_RCA_PROFILE.skill_id: PUCK_RULE_RCA_PROFILE,
    RELEASE_CHECKLIST_PROFILE.skill_id: RELEASE_CHECKLIST_PROFILE,
}


def get_skill_profile(skill_id: str | None) -> SkillProfile:
    return PROFILES.get(str(skill_id or "").lower(), DEFAULT_PROFILE)
