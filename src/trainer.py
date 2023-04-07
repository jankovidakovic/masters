import logging
from typing import Callable, Iterable, Any

import evaluate
import numpy as np
import torch.optim
from torch import nn, binary_cross_entropy_with_logits
from torch.utils.data import DataLoader
from tqdm import tqdm
from transformers import PreTrainedTokenizer


logger = logging.getLogger(__name__)


def default_loss_function(
        input: torch.Tensor,
        target: torch.Tensor
):
    return binary_cross_entropy_with_logits(
        input=input,
        target=target,
        reduction="mean"
    )


def train(
        model: nn.Module,
        tokenizer: PreTrainedTokenizer,
        optimizer: torch.optim.Optimizer,
        train_dataloader: DataLoader,
        eval_dataloader: DataLoader,
        epochs: int,
        eval_steps: int,
        logging_steps: int,
        loss_fn: Callable = default_loss_function,
):
    global_step = 0

    progress_bar = tqdm()  # how the fuck does this work?

    for epoch in range(1, epochs + 1):
        for i, batch in tqdm(enumerate(train_dataloader), total=len(train_dataloader), desc=f"Epoch {epoch}"):
            # realno, za sad mi ne trebaju epohe
            global_step += 1

            output = model(**batch)
            loss = loss_fn(
                input=output["logits"],
                target=batch["labels"],
                reduction="mean"
            )
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()

            if global_step % eval_steps == 0:
                # evaluate
                validation(
                    model,
                    eval_dataloader
                )

            if global_step % logging_steps == 0:
                # log loss
                progress_bar.set_description(desc=f"Loss = {loss}", refresh=True)
                logger.info(f"Step = {global_step} : Loss = {loss}")
                #   this will fuck with tqdm tho, right?

            # I mean, technically this works, right?


def validation(
        model,
        eval_dataloader
):
    # we need some metrics here
    f1_metric = evaluate.load("f1")
    for i, batch in tqdm(enumerate(eval_dataloader), total=len(eval_dataloader), desc="Validation"):
        output = model(**batch)
        # im actually not sure what goes here
        predictions = torch.argmax(output["logits"], )
        f1_metric.add(
        )