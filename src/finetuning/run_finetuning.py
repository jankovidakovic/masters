import logging

import torch
from torch.optim import Adam
from transformers import AutoTokenizer, BertForSequenceClassification

from src.data import setup_data, setup_dataloaders
from src.finetuning import cli
from src.trainer import train
from src.utils import setup_logging, get_labels, get_tokenization_fn

logger = logging.getLogger(__name__)


def main():
    args = cli.get_parser().parse_args()  # okay, legit
    setup_logging(args)

    # lets leave it as it is for now
    logging.info(f"Dataset path = {args.dataframe_path}")

    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer_name if args.tokenizer_name else args.pretrained_model_name_or_path
    )

    tokenization_fn = get_tokenization_fn(
        tokenizer=tokenizer,
        padding=args.padding,
        truncation=True,
        max_length=args.max_length
    )

    labels = get_labels(args)

    train_dataset, eval_dataset = setup_data(args, tokenization_fn, labels)

    # TODO - IterableDataset = ?
    train_dataloader, eval_dataloader = setup_dataloaders(
        train_dataset,
        eval_dataset,
        args
    )

    # initialize model
    model = BertForSequenceClassification.from_pretrained(
        args.pretrained_model_name_or_path,
        num_labels=len(labels),
        problem_type="multi-label"
    )
    model = torch.compile(model)
    # TODO - look into torch.compile options

    logger.info(f"Model loaded successfully.")

    # TODO - make configurable
    optimizer = Adam(
        model.parameters(),
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
        eps=args.adam_epsilon
    )

    train(
        model=model,
        tokenizer=tokenizer,
        optimizer=optimizer,
        train_dataloader=train_dataloader,
        eval_dataloader=eval_dataloader,
        epochs=args.epochs,
        eval_steps=args.eval_steps,
        loss_logging_steps=args.logging_steps
    )

    logger.warning("Training complete.")


if __name__ == "__main__":
    main()

    # okay so MLFlow is basically:
    #   experiment_name -> e.g. "full fine-tuning"
    #   run_name -> e.g.        "fine-tuning on source with hparams x,y,z"
    #
    #   or is it better to have:
    #   experiment_name "fine-tuning on source" and then
    #   run_name "this hparams" -> im not actually sure
    #
    # log:
    #   model
    #   hyperparameters (or just a config file)
    #   results (metrics) -> metrics arent really trivial to log from only training
    #
    #   I should be able to track domain shift while running, no?
    #       -> this could be done using a separate process
    #       -> which basically evaluates the model asynchronously (but that might suck idk)
    #
    # TODO:
    # 1. define exactly what experiments will be running
    # 2.
    #
    # this is basically systems design
    # 1. loading csv file, preprocessing it, saving the processed csv file
    # 2. loading one csv file, performing splitting, saving to multiple csv files
    # 3. train model, given some configuration and some dataset
    # 4. evaluate model, given some model file and some dataset (right?)
