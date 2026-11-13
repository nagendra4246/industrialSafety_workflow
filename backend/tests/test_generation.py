import csv
import hashlib
import json
from pathlib import Path

from app.generation.cli import main
from app.generation.dataset import GenerationConfig, TABLE_ORDER
from app.generation.events import build_baseline_events
from app.generation.generator import generate_dataset
from app.generation.lifecycle import build_lifecycle_history
from app.generation.master_data import build_master_data
from app.generation.validation import validate_dataset


def small_config(seed: int = 20261022) -> GenerationConfig:
    return GenerationConfig(
        seed=seed,
        plant_count=2,
        machine_count=6,
        alarm_definition_count=18,
        alarm_event_count=400,
        days=3,
    )


def generate_valid_baseline(config: GenerationConfig):
    master = build_master_data(config)
    dataset = build_baseline_events(master, config)
    operator_code_by_shift_id = {
        row["id"]: row["operator_code"]
        for row in dataset.tables["operator_shift"]
    }
    build_lifecycle_history(dataset, operator_code_by_shift_id)
    return dataset


def test_valid_baseline_has_no_injected_anomalies_and_passes_applicable_checks():
    dataset = generate_valid_baseline(small_config())
    summary = validate_dataset(dataset, require_edge_cases=False)

    assert summary["status"] == "passed", summary["errors"]
    assert summary["validation_mode"] == "baseline"
    assert dataset.ground_truth == []
    assert len(dataset.replay_deliveries) == len(dataset.tables["alarm_event"])
    assert all(
        row["replay_of_delivery_id"] is None
        for row in dataset.replay_deliveries
    )
    assert all(result["detection_count"] == 0 for result in summary["edge_case_results"])
    assert all(summary["checks"].values())


def test_generator_validates_required_edge_cases_and_source_relationships():
    config = small_config()
    baseline = generate_valid_baseline(config)
    dataset = generate_dataset(config)
    summary = validate_dataset(dataset)
    labels = {row["issue_type"]: row for row in dataset.ground_truth}

    assert summary["status"] == "passed", summary["errors"]
    assert all(summary["checks"].values())
    assert set(labels) == {
        "acknowledgement_before_alarm",
        "impossible_interlock_state",
        "incident_without_triggering_context",
        "replay_duplicated_alarm_flood",
        "action_closed_without_verification",
    }
    results = {row["issue_type"]: row for row in summary["edge_case_results"]}
    assert set(results) == set(labels)
    assert all(
        row["detected"]
        and row["detection_count"] == 1
        and row["label_count"] == 1
        and row["label_match"]
        for row in results.values()
    )
    assert results["replay_duplicated_alarm_flood"]["expected_decision"] == (
        "deduplicate_replayed_alarm_delivery"
    )
    assert summary["acceptance_metrics"]["severe_alarm_prioritization"]["passed"]
    assert {
        row["severity"]
        for row in summary["acceptance_metrics"]["severe_alarm_prioritization"][
            "top_priority_alarm_events"
        ]
    }.issubset({"critical", "high"})
    assert summary["acceptance_metrics"]["nuisance_cluster_identification"]["passed"]
    assert summary["acceptance_metrics"]["incident_traceability"]["passed"]
    incident_open_history = {
        row["entity_id"]: row
        for row in dataset.tables["lifecycle_history"]
        if row["entity_type"] == "incident" and row["transition"] == "opened"
    }
    incident_alarm_ids = {
        row["incident_id"]: row["alarm_event_id"]
        for row in dataset.tables["incident_alarm_event"]
    }
    state_ids = {row["id"] for row in dataset.tables["machine_state_event"]}
    contextless_id = labels["incident_without_triggering_context"]["entity_id"]
    for incident_id, alarm_id in incident_alarm_ids.items():
        contexts = incident_open_history[incident_id]["payload"]["linked_contexts"]
        assert any(
            context["alarm_event_id"] == str(alarm_id)
            and context["machine_state_event_id"] in {
                str(state_id) for state_id in state_ids
            }
            for context in contexts
        )
    assert (
        incident_open_history[contextless_id]["payload"]["linked_contexts"] == []
    )
    replay_result = results["replay_duplicated_alarm_flood"]
    assert replay_result["evidence"][0]["alarm_cluster_size"] >= 3
    baseline_alarm_times = {
        row["id"]: (row["event_at"], row["received_at"])
        for row in baseline.tables["alarm_event"]
    }
    assert baseline_alarm_times == {
        row["id"]: (row["event_at"], row["received_at"])
        for row in dataset.tables["alarm_event"]
    }

    alarms = {row["id"]: row for row in dataset.tables["alarm_event"]}
    acknowledgements = {
        row["id"]: row for row in dataset.tables["acknowledgement"]
    }
    before_alarm = acknowledgements[
        labels["acknowledgement_before_alarm"]["entity_id"]
    ]
    assert before_alarm["acknowledged_at"] < alarms[before_alarm["alarm_event_id"]]["event_at"]

    states = {row["id"]: row for row in dataset.tables["machine_state_event"]}
    impossible_state = states[labels["impossible_interlock_state"]["entity_id"]]
    interlocks = {row["id"]: row for row in dataset.tables["interlock"]}
    assert impossible_state["interlock_state"] != interlocks[impossible_state["interlock_id"]]["required_state"]

    incident_label = labels["incident_without_triggering_context"]
    incident_links = dataset.tables["incident_alarm_event"]
    assert all(link["incident_id"] != incident_label["entity_id"] for link in incident_links)

    replay_deliveries = dataset.replay_deliveries
    assert len(replay_deliveries) == len(dataset.tables["alarm_event"]) + 1
    assert len({row["external_event_id"] for row in replay_deliveries}) == len(dataset.tables["alarm_event"])

    actions = {row["id"]: row for row in dataset.tables["corrective_action"]}
    unverified_action = actions[labels["action_closed_without_verification"]["entity_id"]]
    assert unverified_action["status"] == "closed"
    assert unverified_action["verification_result"] == "not_started"
    assert any(
        row["severity"] in {"high", "critical"}
        for row in dataset.tables["alarm_definition"]
    )
    assert dataset.row_counts()["alarm_event"] == 400


def test_ground_truth_labels_only_evaluate_source_derived_findings():
    dataset = generate_dataset(small_config())
    labeled = validate_dataset(dataset)
    findings = labeled["detected_edge_case_counts"]
    metrics = labeled["acceptance_metrics"]

    dataset.ground_truth = []
    unlabeled = validate_dataset(dataset)

    assert unlabeled["detected_edge_case_counts"] == findings
    for metric in (
        "severe_alarm_prioritization",
        "nuisance_cluster_identification",
        "incident_traceability",
    ):
        assert unlabeled["acceptance_metrics"][metric] == metrics[metric]
    assert (
        unlabeled["acceptance_metrics"]["coherent_timeline"][
            "known_acknowledgement_chronology_exceptions"
        ]
        == metrics["coherent_timeline"][
            "known_acknowledgement_chronology_exceptions"
        ]
    )
    assert unlabeled["acceptance_metrics"]["coherent_timeline"]["passed"] is False
    assert unlabeled["status"] == "failed"
    assert all(
        not result["label_match"]
        for result in unlabeled["edge_case_results"]
        if result["detected"]
    )


def test_exports_are_reproducible_and_keep_ground_truth_out_of_seed_sql(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    for output_dir in (first_dir, second_dir):
        assert main(
            [
                "--seed",
                "23",
                "--plant-count",
                "2",
                "--machine-count",
                "6",
                "--alarm-definition-count",
                "18",
                "--alarm-event-count",
                "400",
                "--days",
                "3",
                "--output",
                str(output_dir),
            ]
        ) == 0

    first_checksums = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in first_dir.iterdir()
        if path.is_file()
    }
    second_checksums = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in second_dir.iterdir()
        if path.is_file()
    }
    assert first_checksums == second_checksums

    manifest = json.loads((first_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_counts"]["alarm_event"] == 400
    assert manifest["row_counts"]["ground_truth"] == 5
    assert manifest["source_manifest"]["external_sources"] == []
    assert set(manifest["checksums"]) == set(first_checksums) - {"manifest.json"}
    assert "validation_report.md" in manifest["checksums"]
    dictionary_candidates = (
        Path.cwd() / "docs" / "data-dictionary.md",
        Path.cwd().parent / "docs" / "data-dictionary.md",
    )
    source_dictionary = next(
        candidate for candidate in dictionary_candidates if candidate.is_file()
    )
    assert (first_dir / "data-dictionary.md").read_bytes() == source_dictionary.read_bytes()

    with (first_dir / "alarm_event.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        assert sum(1 for _ in csv.DictReader(source)) == 400
    assert len(json.loads((first_dir / "ground_truth.json").read_text())) == 5
    validation_report = (first_dir / "validation_report.md").read_text(
        encoding="utf-8"
    )
    assert "Labelled edge-case detections" in validation_report
    assert "acknowledgement_before_alarm" in validation_report
    assert "Nuisance clusters identified" in validation_report
    seed_sql = (first_dir / "seed.sql").read_text(encoding="utf-8")
    assert "GT-000001" not in seed_sql
    assert "ground_truth" not in seed_sql
    assert "replay_deliveries" not in seed_sql


def test_cli_default_scale_and_output_path(tmp_path):
    config = GenerationConfig()
    assert (config.seed, config.plant_count, config.machine_count) == (
        20261022,
        4,
        60,
    )
    assert (config.alarm_definition_count, config.alarm_event_count, config.days) == (
        200,
        50_000,
        30,
    )

    output_dir = tmp_path / "cli-output"
    assert main(
        [
            "--plant-count",
            "1",
            "--machine-count",
            "3",
            "--alarm-definition-count",
            "9",
            "--alarm-event-count",
            "40",
            "--days",
            "2",
            "--output",
            str(output_dir),
        ]
    ) == 0
    summary = json.loads((output_dir / "validation_summary.json").read_text())
    assert summary["status"] == "passed"
    assert summary["row_counts"]["alarm_event"] == 40
    assert set(summary["row_counts"]) == set(TABLE_ORDER)
    assert (output_dir / "validation_report.md").is_file()
