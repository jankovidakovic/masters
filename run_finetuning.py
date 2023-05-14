import logging
import os

import hydra
import pandas as pd
import torch
from omegaconf import DictConfig, OmegaConf
from pandas import DataFrame
from transformers import (
    set_seed,
    AutoModelForSequenceClassification,
)

from src.preprocess.steps import multihot_to_list, to_hf_dataset, hf_map, convert_to_torch, sequence_columns
from src.trainer import train, fine_tuning_loss, evaluate_finetuning
from src.utils import get_labels, get_tokenization_fn, setup_optimizers, maybe_tf32, get_tokenizer, \
    pipeline, mean_binary_cross_entropy


logger = logging.getLogger(__name__)


@hydra.main(version_base="1.3", config_path="configs", config_name="finetuning")
def main(args: DictConfig):
    # lets see if this actually works now
    # args = parse_args("finetuning")
    logger.info(OmegaConf.to_yaml(args))

    os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    # removed setup_logging because hydra does that for me

    set_seed(args.random_seed)  # oh but this actually already works because argparse has __getattr__ implemented

    maybe_tf32(args)

    tokenizer = get_tokenizer(args)

    do_tokenize = get_tokenization_fn(
        tokenizer=tokenizer,
        padding=args.padding,  # noqa
        truncation=True,
        max_length=args.max_length,
        message_column=args.message_column
    )
    labels = get_labels(args.labels_path)

    do_preprocess = pipeline(
        pd.read_csv,
        DataFrame.dropna,
        multihot_to_list(
            label_columns=labels,
            result_column="labels"
        ),
        to_hf_dataset,
        hf_map(do_tokenize, batched=True),
        convert_to_torch(columns=sequence_columns)
    )

    train_dataset = do_preprocess(args.train_dataset_path)
    eval_dataset = do_preprocess(args.eval_dataset_path)

    # initialize model
    model = AutoModelForSequenceClassification.from_pretrained(
        args.pretrained_model_name_or_path,
        num_labels=len(labels),
        cache_dir=args.cache_dir,
        problem_type="multi_label_classification"
    )

    if args.use_torch_compile:
        model = torch.compile(model)

    model = model.to(args.device)
    # TODO - look into torch.compile options

    logger.info(f"Model loaded successfully on device: {model.device}")

    epoch_steps = len(train_dataset) // args.per_device_train_batch_size // args.gradient_accumulation_steps

    optimizer, scheduler = setup_optimizers(
        model,
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
        adam_epsilon=args.adam_epsilon,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        warmup_percentage=args.warmup_percentage,
        epochs=args.epochs,
        epoch_steps=epoch_steps,
        scheduler_type=args.scheduler_type
    )

    if use_mlflow := hasattr(args, "mlflow"):
        import mlflow
        mlflow.set_tracking_uri(args.mlflow.tracking_uri)
        mlflow_experiment = mlflow.set_experiment(args.mlflow.experiment)

        mlflow.start_run(
            experiment_id=mlflow_experiment.experiment_id,
            run_name=args.mlflow.run_name,
            description=args.mlflow.run_description
        )

        # aha i can actually log to hydra, right?

        # log run_id to logging file, to be found later by grep
        logger.info(f"MLFlow run_id={mlflow.active_run().info.run_id}")

        # mlflow.log_params(vars(args))
        mlflow.log_dict(OmegaConf.to_container(args, resolve=True), "args")

    # this can be "with maybe_mlflow(args)"
    # or it can be a decorator

    train(
        model=model,
        optimizer=optimizer,
        scheduler=scheduler,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,
        epochs=args.epochs,
        max_grad_norm=args.max_grad_norm,
        do_evaluate=evaluate_finetuning(
            evaluation_threshold=args.evaluation_threshold,
        ),
        get_loss=fine_tuning_loss(loss_fn=mean_binary_cross_entropy),
        early_stopping_patience=args.early_stopping_patience,
        metric_for_best_model=args.metric_for_best_model,
        greater_is_better=args.greater_is_better,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        use_mlflow=use_mlflow,
        evaluate_on_train=args.evaluate_on_train,
        early_stopping_start=args.early_stopping_start,
        dataloader_num_workers=args.dataloader_num_workers,
    )

    logger.warning("Training complete.")

    if use_mlflow:
        mlflow.end_run()


if __name__ == "__main__":
    main()
