"""
Proximal Policy Optimization (PPO) - Accelerated with Lightning Fabric
Adapted from https://github.com/vwxyzjn/cleanrl/blob/master/cleanrl/ppo.py
Based on the paper: https://arxiv.org/abs/1707.06347
Requirements:
- lightning>=1.9.0
- gymnasium==0.26.3
- torch
- tensorboard
Run it with:
    torchrun --nproc_per_node=2 --standalone train_torch.py
"""

import argparse
import os
import random
import time
from typing import Dict, Tuple

import gymnasium as gym
import numpy as np
import torch
import torch.distributed as distributed
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions.categorical import Categorical
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import BatchSampler, DistributedSampler
from torch.utils.tensorboard import SummaryWriter

from src.loss import entropy_loss, policy_loss, value_loss
from src.utils import layer_init, make_env, parse_args


class PPOAgent(torch.nn.Module):
    def __init__(
        self,
        envs: gym.vector.SyncVectorEnv,
        act_fun: str = "relu",
        ortho_init: bool = False,
        vf_coef: float = 0.5,
        ent_coef: float = 0.01,
        clip_coef: float = 0.2,
        clip_vloss: bool = False,
        normalize_advantages: bool = False,
    ) -> None:
        super().__init__()
        if act_fun.lower() == "relu":
            act_fun = torch.nn.ReLU()
        elif act_fun.lower() == "tanh":
            act_fun = torch.nn.Tanh()
        else:
            raise ValueError("Unrecognized activation function: `act_fun` must be either `relu` or `tanh`")
        self.vf_coef = vf_coef
        self.ent_coef = ent_coef
        self.clip_coef = clip_coef
        self.clip_vloss = clip_vloss
        self.normalize_advantages = normalize_advantages
        self.critic = torch.nn.Sequential(
            layer_init(
                torch.nn.Linear(np.array(envs.single_observation_space.shape).prod(), 64), ortho_init=ortho_init
            ),
            act_fun,
            layer_init(torch.nn.Linear(64, 64), ortho_init=ortho_init),
            act_fun,
            layer_init(torch.nn.Linear(64, 1), std=1.0, ortho_init=ortho_init),
        )
        self.actor = torch.nn.Sequential(
            layer_init(
                torch.nn.Linear(np.array(envs.single_observation_space.shape).prod(), 64), ortho_init=ortho_init
            ),
            act_fun,
            layer_init(torch.nn.Linear(64, 64), ortho_init=ortho_init),
            act_fun,
            layer_init(torch.nn.Linear(64, envs.single_action_space.n), std=0.01, ortho_init=ortho_init),
        )

    def get_action(
        self, x: torch.Tensor, action: torch.Tensor = None
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits = self.actor(x)
        distribution = Categorical(logits=logits)
        if action is None:
            action = distribution.sample()
        return action, distribution.log_prob(action), distribution.entropy()

    def get_greedy_action(self, x: torch.Tensor) -> torch.Tensor:
        logits = self.actor(x)
        probs = F.softmax(logits, dim=-1)
        return torch.argmax(probs, dim=-1)

    def get_value(self, x: torch.Tensor) -> torch.Tensor:
        return self.critic(x)

    def get_action_and_value(
        self, x: torch.Tensor, action: torch.Tensor = None
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        action, log_prob, entropy = self.get_action(x, action)
        value = self.get_value(x)
        return action, log_prob, entropy, value

    def forward(
        self, x: torch.Tensor, action: torch.Tensor = None
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        return self.get_action_and_value(x, action)

    @torch.no_grad()
    def estimate_returns_and_advantages(
        self,
        rewards: torch.Tensor,
        values: torch.Tensor,
        dones: torch.Tensor,
        next_obs: torch.Tensor,
        next_done: torch.Tensor,
        num_steps: int,
        gamma: float,
        gae_lambda: float,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        next_value = self.get_value(next_obs).reshape(1, -1)
        advantages = torch.zeros_like(rewards)
        lastgaelam = 0
        for t in reversed(range(num_steps)):
            if t == num_steps - 1:
                nextnonterminal = 1.0 - next_done
                nextvalues = next_value
            else:
                nextnonterminal = 1.0 - dones[t + 1]
                nextvalues = values[t + 1]
            delta = rewards[t] + gamma * nextvalues * nextnonterminal - values[t]
            advantages[t] = lastgaelam = delta + gamma * gae_lambda * nextnonterminal * lastgaelam
        returns = advantages + values
        return returns, advantages


def train(
    agent: PPOAgent,
    optimizer: torch.optim.Optimizer,
    data: Dict[str, torch.Tensor],
    logger: SummaryWriter,
    global_step: int,
    args: argparse.Namespace,
):
    sampler = DistributedSampler(
        list(range(data["obs"].shape[0])),
        num_replicas=distributed.get_world_size(),
        rank=distributed.get_rank(),
        shuffle=True,
        seed=args.seed,
    )
    sampler = BatchSampler(sampler, batch_size=args.per_rank_batch_size, drop_last=False)

    for epoch in range(args.update_epochs):
        sampler.sampler.set_epoch(epoch)
        for batch_idxes in sampler:
            _, newlogprob, entropy, newvalue = agent(data["obs"][batch_idxes], data["actions"].long()[batch_idxes])
            logratio = newlogprob - data["logprobs"][batch_idxes]
            ratio = logratio.exp()

            advantages = data["advantages"][batch_idxes]
            if args.normalize_advantages:
                advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

            # Policy loss
            pg_loss = policy_loss(advantages, ratio, args.clip_coef)

            # Value loss
            v_loss = value_loss(
                newvalue,
                data["values"][batch_idxes],
                data["returns"][batch_idxes],
                args.clip_coef,
                args.clip_vloss,
                args.vf_coef,
            )

            # Entropy loss
            ent_loss = entropy_loss(entropy, args.ent_coef)

            # Overall loss
            loss = pg_loss + ent_loss + v_loss

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(agent.parameters(), args.max_grad_norm)
            optimizer.step()

            # Log
            if logger is not None:
                logger.add_scalar("Loss/value_loss", v_loss, global_step)
                logger.add_scalar("Loss/policy_loss", pg_loss, global_step)
                logger.add_scalar("Loss/entropy", ent_loss, global_step)


@torch.no_grad()
def test(agent: PPOAgent, device: torch.device, logger: SummaryWriter, args: argparse.Namespace):
    env = make_env(args.env_id, args.seed, 0, args.capture_video, logger.log_dir)()
    step = 0
    done = False
    cumulative_rew = 0
    next_obs = torch.Tensor(env.reset(seed=args.seed)[0]).to(device)
    while not done:
        # Act greedly through the environment
        action = agent.module.get_greedy_action(next_obs)

        # Single environment step
        next_obs, reward, done, truncated, info = env.step(action.cpu().numpy())
        done = np.logical_or(done, truncated)
        cumulative_rew += reward
        next_obs = torch.Tensor(next_obs).to(device)
        step += 1
    logger.add_scalar(f"Test/cumulative_reward", cumulative_rew, 0)
    env.close()


def main(args: argparse.Namespace):
    # Init distributed environment
    is_cuda_available = torch.cuda.is_available()
    backend = "nccl" if is_cuda_available else "gloo"
    local_rank = int(os.environ["LOCAL_RANK"])
    global_rank = int(os.environ["RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    if is_cuda_available:
        torch.cuda.set_device(local_rank)
    device = torch.device(f"cuda:{local_rank}" if is_cuda_available else "cpu")
    os.environ["MASTER_ADDR"] = "localhost"
    os.environ["MASTER_PORT"] = "12345"
    distributed.init_process_group(backend=backend)

    # Seed everything
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    # Logger
    log_dir = None
    logger = None
    run_name = f"{args.env_id}_{args.exp_name}_{args.seed}_{int(time.time())}"
    if global_rank == 0:
        log_dir = os.path.join("torch_logs", run_name)
        logger = SummaryWriter(log_dir=log_dir)

    # Log hyperparameters
    if global_rank == 0:
        logger.add_text(
            "hyperparameters",
            "|param|value|\n|-|-|\n%s" % ("\n".join([f"|{key}|{value}|" for key, value in vars(args).items()])),
        )

    # Environment setup
    envs = gym.vector.SyncVectorEnv(
        [
            make_env(args.env_id, args.seed + global_rank, global_rank, args.capture_video, log_dir)
            for _ in range(args.per_rank_num_envs)
        ]
    )
    assert isinstance(envs.single_action_space, gym.spaces.Discrete), "only discrete action space is supported"

    # Define the agent and the optimizer and setup them with Fabric
    agent: PPOAgent = PPOAgent(
        envs,
        act_fun=args.activation_function,
        vf_coef=args.vf_coef,
        ent_coef=args.ent_coef,
        clip_coef=args.clip_coef,
        clip_vloss=args.clip_vloss,
        ortho_init=args.ortho_init,
        normalize_advantages=args.normalize_advantages,
    ).to(device)
    agent = DistributedDataParallel(
        agent,
        device_ids=[local_rank] if is_cuda_available else None,
        output_device=local_rank if is_cuda_available else None,
    )
    optimizer = optim.Adam(agent.parameters(), lr=args.learning_rate, eps=1e-5)

    # Local data
    obs = torch.zeros((args.num_steps, args.per_rank_num_envs) + envs.single_observation_space.shape, device=device)
    actions = torch.zeros((args.num_steps, args.per_rank_num_envs) + envs.single_action_space.shape, device=device)
    logprobs = torch.zeros((args.num_steps, args.per_rank_num_envs), device=device)
    rewards = torch.zeros((args.num_steps, args.per_rank_num_envs), device=device)
    dones = torch.zeros((args.num_steps, args.per_rank_num_envs), device=device)
    values = torch.zeros((args.num_steps, args.per_rank_num_envs), device=device)

    # Global variables
    global_step = 0
    start_time = time.time()
    single_global_step = int(args.per_rank_num_envs * args.num_steps * world_size)
    num_updates = args.total_timesteps // single_global_step

    # Get the first environment observation and start the optimization
    next_obs = torch.Tensor(envs.reset(seed=args.seed)[0]).to(device)
    next_done = torch.zeros(args.per_rank_num_envs, device=device)
    for update in range(1, num_updates + 1):
        # Learning rate annealing
        if global_rank == 0:
            logger.add_scalar("Info/learning_rate", optimizer.param_groups[0]["lr"], global_step)
        if args.anneal_lr:
            frac = 1.0 - (update - 1.0) / num_updates
            lrnow = frac * args.learning_rate
            optimizer.param_groups[0]["lr"] = lrnow

        for step in range(0, args.num_steps):
            global_step += args.per_rank_num_envs * world_size
            obs[step] = next_obs
            dones[step] = next_done

            # Sample an action given the observation received by the environment
            with torch.no_grad():
                action, logprob, _, value = agent(next_obs)
                values[step] = value.flatten()
            actions[step] = action
            logprobs[step] = logprob

            # Single environment step
            next_obs, reward, done, truncated, info = envs.step(action.cpu().numpy())
            done = np.logical_or(done, truncated)
            rewards[step] = torch.tensor(reward, device=device).view(-1)
            next_obs, next_done = torch.Tensor(next_obs).to(device), torch.Tensor(done).to(device)

            if global_rank == 0 and "final_info" in info:
                for agent_id, agent_final_info in enumerate(info["final_info"]):
                    if agent_final_info is not None and "episode" in agent_final_info:
                        print(
                            f"global_step={global_step}, reward_agent_{agent_id}={agent_final_info['episode']['r'][0]}"
                        )
                        logger.add_scalar(
                            f"Rewards/agent_{agent_id}",
                            agent_final_info["episode"]["r"][0],
                            global_step,
                        )
                        logger.add_scalar(
                            f"Game/episode_length_agent_{agent_id}",
                            agent_final_info["episode"]["l"][0],
                            global_step,
                        )

        # Estimate returns with GAE (https://arxiv.org/abs/1506.02438)
        returns, advantages = agent.module.estimate_returns_and_advantages(
            rewards, values, dones, next_obs, next_done, args.num_steps, args.gamma, args.gae_lambda
        )

        # Flatten the batch
        local_data = {
            "obs": obs.reshape((-1,) + envs.single_observation_space.shape),
            "logprobs": logprobs.reshape(-1),
            "actions": actions.reshape((-1,) + envs.single_action_space.shape),
            "advantages": advantages.reshape(-1),
            "returns": returns.reshape(-1),
            "values": values.reshape(-1),
        }

        # Gather all the tensors from all the world, concat and reshape them
        gathered_data = [None for _ in range(world_size)]
        distributed.all_gather_object(gathered_data, local_data)
        processed_gathered_data = gathered_data[0]
        for i in range(1, len(gathered_data)):
            for k in processed_gathered_data.keys():
                processed_gathered_data[k] = torch.cat(
                    (processed_gathered_data[k].to(device), gathered_data[i][k].to(device)), dim=0
                )

        # Train the agent
        train(agent, optimizer, processed_gathered_data, logger, global_step, args)
        if global_rank == 0:
            logger.add_scalar("Time/step_per_second", int(global_step / (time.time() - start_time)), global_step)

    envs.close()
    if global_rank == 0:
        test(agent, device, logger, args)


if __name__ == "__main__":
    args = parse_args()
    main(args)
