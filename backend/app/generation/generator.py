from __future__ import annotations

from .dataset import GenerationConfig, SyntheticDataset
from .edge_cases import inject_known_edge_cases
from .events import build_baseline_events
from .lifecycle import build_lifecycle_history
from .master_data import MasterData, build_master_data


def generate_dataset(config: GenerationConfig | None = None) -> SyntheticDataset:
    config = config or GenerationConfig()
    master: MasterData = build_master_data(config)
    dataset = build_baseline_events(master, config)
    inject_known_edge_cases(master, config, dataset)
    operator_code_by_shift_id = {
        shift["id"]: shift["operator_code"]
        for shift in dataset.tables["operator_shift"]
    }
    build_lifecycle_history(dataset, operator_code_by_shift_id)
    return dataset
