from __future__ import annotations

import json
from pathlib import Path

from kokochi_ui_agent_readability_lab.evaluation.group_membership import (
    evaluate_group_membership_response,
    load_group_membership_ground_truth,
    normalize_group_label,
    response_json_schema,
)


ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH = (
    ROOT
    / "experiments"
    / "form-group-membership-reconstruction"
    / "ground-truth"
    / "v1.json"
)


def _ground_truth():
    return load_group_membership_ground_truth(GROUND_TRUTH.read_bytes())


def test_ground_truth_partitions_every_control() -> None:
    ground_truth = _ground_truth()

    assert {task.task_id for task in ground_truth.tasks} == {
        "person-group-membership",
        "purpose-group-membership",
    }
    assert [len(task.eligible_control_indices) for task in ground_truth.tasks] == [
        18,
        29,
    ]


def test_scores_exact_person_membership() -> None:
    response = {
        "memberships": [
            *(
                {"control_index": index, "group_label": "あなたの情報"}
                for index in range(1, 13)
            ),
            *(
                {"control_index": index, "group_label": "緊急連絡先"}
                for index in range(13, 19)
            ),
        ],
        "ungrouped_control_indices": [],
    }

    normalized, score = evaluate_group_membership_response(
        json.dumps(response, ensure_ascii=False).encode(),
        ground_truth=_ground_truth(),
        fixture_id="person-context-semantic-first",
    )

    assert normalized is not None
    assert score.schema_valid
    assert score.complete_response
    assert score.exact_match
    assert score.f1 == 1.0


def test_scores_group_boundary_error_as_relation_error() -> None:
    response = {
        "memberships": [
            *(
                {"control_index": index, "group_label": "あなたの情報"}
                for index in range(1, 14)
            ),
            *(
                {"control_index": index, "group_label": "緊急連絡先"}
                for index in range(14, 19)
            ),
        ],
        "ungrouped_control_indices": [],
    }

    _, score = evaluate_group_membership_response(
        json.dumps(response, ensure_ascii=False).encode(),
        ground_truth=_ground_truth(),
        fixture_id="person-context-presentation-first",
    )

    assert score.complete_response
    assert not score.exact_match
    assert score.true_positive_count == 17
    assert score.false_positive_count == 1
    assert score.false_negative_count == 1
    assert score.f1 == 17 / 18


def test_invalid_json_is_retained_as_zero_score() -> None:
    normalized, score = evaluate_group_membership_response(
        b'{"memberships": [',
        ground_truth=_ground_truth(),
        fixture_id="purpose-context-semantic-first",
    )

    assert normalized is None
    assert not score.schema_valid
    assert score.f1 == 0.0
    assert len(score.missing_control_indices) == 29


def test_normalization_and_schema_do_not_disclose_fixture_answers() -> None:
    assert normalize_group_label("  お問い合わせ\u3000先 ") == "お問い合わせ 先"
    schema = json.dumps(response_json_schema(), ensure_ascii=False)
    assert "maximum" not in schema
    assert "配送先" not in schema
