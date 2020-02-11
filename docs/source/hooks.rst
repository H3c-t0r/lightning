Hooks
=======
This is the order in which lightning calls the hooks. You can override each for custom behavior.

Training set-up
--------------------
- init_ddp_connection
- init_optimizers
- configure_apex
- configure_ddp
- get_train_dataloader
- get_test_dataloaders
- get_val_dataloaders
- summarize
- restore_weights

Training loop
--------------------

- on_epoch_start
- on_batch_start
- tbptt_split_batch
- training_step
- training_end (optional)
- backward
- on_after_backward
- optimizer.step()
- on_batch_end
- on_epoch_end

Validation loop
--------------------

- model.zero_grad()
- model.eval()
- torch.set_grad_enabled(False)
- validation_step
- validation_end
- model.train()
- torch.set_grad_enabled(True)
- on_post_performance_check

Test loop
------------

- model.zero_grad()
- model.eval()
- torch.set_grad_enabled(False)
- test_step
- test_end
- model.train()
- torch.set_grad_enabled(True)
- on_post_performance_check