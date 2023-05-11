import gc
import logging
import os.path
from argparse import ArgumentParser
from pprint import pformat

import mlflow
import pandas as pd
from pandas import DataFrame
from torch.utils.data import DataLoader
from transformers import AutoTokenizer, AutoModelForSequenceClassification, DefaultDataCollator

from src.preprocess.steps import multihot_to_list, to_hf_dataset, hf_map, convert_to_torch, sequence_columns
from src.trainer import evaluate_finetuning
from src.utils import setup_logging, get_labels, get_tokenization_fn, pipeline

import torch

logger = logging.getLogger(__name__)


def main():

    os.environ["TOKENIZERS_PARALLELISM"] = "0"
    os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"


    parser = ArgumentParser("Evaluation of saved checkpoint on multiple domains")

    parser.add_argument(
        "--checkpoint",
        type=str,
        help="Paths to the checkpoints to evaluate.",
    )

    parser.add_argument(
        "--dataset_paths",
        type=str,
        nargs="+",
        help="Paths to the datasets to evaluate on.",
    )

    parser.add_argument(
        "--dataset_names",
        type=str,
        nargs="+",
        help="Names to use for the datasets. Metrics will be logged with "
             "the provided names as prefixes.",
    )

    parser.add_argument(
        "--mlflow_run_id",
        type=str,
        help="MLFlow run ID to log metrics to."
    )
    parser.add_argument(
        "--mlflow_tracking_uri",
        type=str,
        default="http://localhost:34567",
        help="MLFlow tracking URI."
    )

    parser.add_argument(
        "--labels_path",
        type=str,
        default="./labels.json",
        help="Path to JSON file containing the labels."
    )

    parser.add_argument(
        "--batch_size",
        type=int,
        default=128,
        help="Batch size for evaluation. Default is 128."
    )

    args = parser.parse_args()

    setup_logging(None)

    # load labels
    labels = get_labels(args.labels_path)

    mlflow.set_tracking_uri(args.mlflow_tracking_uri)
    mlflow.start_run(
        run_id=args.mlflow_run_id,
    )

    checkpoint_name = os.path.abspath(args.checkpoint)
    logger.warning(f"Running evaluation for checkpoint: {checkpoint_name}")

    # extract checkpoint step
    checkpoint_step = args.checkpoint.split("/")[-1].split("-")[-1]

    for dataset_name, dataset_path in zip(args.dataset_names, args.dataset_paths):
        # evaluate checkpoint on dataset
        tokenizer = AutoTokenizer.from_pretrained(
            args.checkpoint,
            model_max_length=64,
            do_lower_case=True,
        )

        do_tokenize = get_tokenization_fn(
            tokenizer=tokenizer,
            padding="max_length",
            truncation=True,
            max_length=64,
            message_column="preprocessed"
        )

        # load model
        model = AutoModelForSequenceClassification.from_pretrained(
            args.checkpoint,
            problem_type="multi_label_classification",
            num_labels=len(labels)
        )  # sumnjivo tho  -- ma moze

        model = model.to("cuda")
        # removed torch.compile because its not even faster and it doesnt really work with adapters

        logger.warning(f"Model successfully loaded. ")

        # load datasets
        do_preprocess = pipeline(
            pd.read_csv,
            multihot_to_list(
                label_columns=labels,
                result_column="labels"
            ),
            to_hf_dataset,
            hf_map(do_tokenize, batched=True),
            convert_to_torch(columns=sequence_columns)
        )

        dataset = do_preprocess(dataset_path)

        logger.warning(f"Dataset {dataset_name} processed successfully.")

        dataloader = DataLoader(
            dataset,
            batch_size=args.batch_size,
            shuffle=False,
            num_workers=4,
            pin_memory=True,
            collate_fn=DefaultDataCollator(return_tensors="pt")
        )

        do_evaluate = evaluate_finetuning(
            # default threshold
            # default loss_fn
        )

        logger.warning(f"Evaluating on dataset: {dataset_name}")

        metrics = do_evaluate(
            model=model,
            eval_dataloader=dataloader,
            prefix=dataset_name
        )

        logger.warning(pformat(metrics))
        mlflow.log_metrics(
            metrics,
            step=int(checkpoint_step)
        )

        logger.warning(f"evaluation finished for {dataset_name}")

    logger.warning(f"Evaluated all datasets on checkpoint {args.checkpoint}")
    mlflow.end_run()


if __name__ == '__main__':
    main()
