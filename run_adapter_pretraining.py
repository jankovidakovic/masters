import logging
import os
from functools import partial

import pandas as pd
from math import ceil
from pandas import DataFrame
from transformers import set_seed, DataCollatorForLanguageModeling, AutoModelForMaskedLM, AdapterConfig, \
    AutoAdapterModel
import torch

from src.cli import parse_args
from src.preprocess import hf_map, to_hf_dataset, sequence_columns, convert_to_torch
from src.trainer import train, pretraining_loss, evaluate_pretraining
from src.utils import setup_logging, maybe_tf32, get_tokenizer, get_tokenization_fn, pipeline, setup_optimizers, \
    save_adapter_model

logger = logging.getLogger(__name__)


def main():
    # setup logging
    args = parse_args("pretraining", use_adapters=True)
    setup_logging(args)
    set_seed(args.random_seed)
    maybe_tf32(args)

    tokenizer = get_tokenizer(args)

    do_tokenize = get_tokenization_fn(
        tokenizer=tokenizer,
        padding=args.padding,  # noqa
        truncation=True,
        max_length=args.max_length,
        return_special_tokens_mask=True,
        message_column=args.message_column
    )  # would be cool to abstract this also

    do_preprocess = pipeline(
        pd.read_csv,
        DataFrame.dropna,
        to_hf_dataset,
        hf_map(do_tokenize, batched=True),
        convert_to_torch(columns=sequence_columns)
    )

    train_dataset = do_preprocess(args.train_dataset_path)
    eval_dataset = do_preprocess(args.eval_dataset_path)

    # initialize model
    model = AutoAdapterModel.from_pretrained(
        args.pretrained_model_name_or_path,
        cache_dir=args.cache_dir,
    )
    # we start from the model which is already pretrained

    adapter_config = AdapterConfig.load(args.adapter_config, reduction_factor=args.reduction_factor)

    logger.warning(f"Loaded the following adapter config: {adapter_config}")
    model.add_adapter(args.adapter_name, adapter_config)
    model.add_masked_lm_head(args.adapter_name)
    model.train_adapter(args.adapter_name)
    logger.warning(model.adapter_summary())

    # compile model
    if args.use_torch_compile:
        model = torch.compile(model)

    model = model.to(args.device)

    logger.info(f"Model loaded successfully on device: {model.device}")

    epoch_steps = ceil(len(train_dataset) / args.per_device_train_batch_size / args.gradient_accumulation_steps)

    # TODO - make configurable
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

    if args.mlflow_experiment:
        use_mlflow = True
        import mlflow
        # set up mlflow
        mlflow.set_tracking_uri(args.mlflow_tracking_uri)
        mlflow_experiment = mlflow.set_experiment(args.mlflow_experiment)

        mlflow.start_run(
            experiment_id=mlflow_experiment.experiment_id,
            run_name=args.mlflow_run_name,
            description=args.mlflow_run_description
        )

        os.makedirs(args.output_dir, exist_ok=True)
        with open(f"{args.output_dir}/mlflow_run_id.txt", "w") as f:
            f.write(mlflow.active_run().info.run_id)

        mlflow.log_params(vars(args))

    else:
        use_mlflow = False

    train(
        model=model,
        tokenizer=tokenizer,
        optimizer=optimizer,
        scheduler=scheduler,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        collate_fn=DataCollatorForLanguageModeling(
            tokenizer=tokenizer,
            mlm=True,
            mlm_probability=args.mlm_probability,
        ),
        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,
        epochs=args.epochs,
        save_steps=args.save_steps,
        output_dir=args.output_dir,
        max_grad_norm=args.max_grad_norm,
        early_stopping_patience=args.early_stopping_patience,
        metric_for_best_model=args.metric_for_best_model,
        greater_is_better=args.greater_is_better,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        get_loss=pretraining_loss(),
        do_evaluate=evaluate_pretraining(),
        use_mlflow=use_mlflow,
        model_saving_callback=partial(save_adapter_model, adapter_name=args.adapter_name),
        dataloader_num_workers=args.dataloader_num_workers,
    )

    logger.warning("Training complete.")

    if use_mlflow:
        mlflow.end_run()


if __name__ == "__main__":
    main()
