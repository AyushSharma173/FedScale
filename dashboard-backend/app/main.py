# dashboard-backend/app/main.py

import uuid
import subprocess
import copy
import multiprocessing
from typing import Dict, List
import grpc
from grpc import RpcError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fedscale.cloud.config_parser import args as base_args
import fedscale.cloud.commons as commons
from fedscale.cloud.aggregation.aggregator import Aggregator
from fedscale.cloud.execution.executor import Executor

import grpc
from fedscale.cloud.channels import job_api_pb2, job_api_pb2_grpc

import asyncio, json
from fastapi.responses import StreamingResponse

# somewhere at module scope, or per‐request:
# channel = grpc.insecure_channel("127.0.0.1:50051")
# stub   = job_api_pb2_grpc.JobServiceStub(channel)


app = FastAPI(
    title="FedScale Dashboard API",
    version="0.1.0",
    description="Expose client & aggregator state & control for the React dashboard"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Can also use Redis or SQLite later
metrics_history = {} # exp_id -> list of metrics_payload

# ---- Data models ----

# class ExperimentStartRequest(BaseModel):
#     name: str
#     num_executors: int = 1
#     num_clients: int
    

class ExperimentStartRequest(BaseModel):
    name: str

    # Cluster scale & aggregation
    num_executors:        int = 1
    num_clients:          int
    gradient_policy:      str = "FedAvg"
    experiment_mode:      str = "sync"
    backend:              str = "gloo"
    engine:               str = "PyTorch"

    # Model & data selection
    model_zoo:            str
    model:                str
    data_set:             str
    data_dir:             str
    input_shape:          str  # e.g. "3,224,224"
    output_dim:           int
    num_classes:          int
    embedding_file:       str = ""

    # Training-loop hyperparameters
    rounds:               int = 1
    local_steps:          int = 1
    batch_size:           int = 8
    test_bsz:             int = 8
    learning_rate:        float = 0.01
    min_learning_rate:    float = 0.001
    decay_factor:         float = 0.1
    decay_round:          int = 1
    clip_bound:           float = 1.0
    eval_interval:        int = 1
    dump_epoch:           int = 1

class ExperimentStatus(BaseModel):
    id: str
    name: str
    num_clients: int
    processes: List[int]  # PIDs
    running: bool


# ---- In-memory registry ----
# maps experiment_id -> dict with keys matching ExperimentStatus
_experiments: Dict[str, Dict] = {}


# ---- worker spawn functions ----

def _run_aggregator(cfg_overrides: dict):
    # deep copy the base namespace, then override
    args = copy.deepcopy(base_args)
    for k, v in cfg_overrides.items():
        setattr(args, k, v)
    Aggregator(args).run()


def _run_executor(exp_id: str, rank: int, cfg_overrides: dict):
    args = copy.deepcopy(base_args)
    args.experiment_mode = commons.SIMULATION_MODE
    args.this_rank = rank
    args.num_executors = 1
    for k, v in cfg_overrides.items():
        setattr(args, k, v)
    Executor(args).run()


def get_stub():
    # channel = grpc.insecure_channel("127.0.0.1:50051")
    channel = grpc.insecure_channel(
        "127.0.0.1:50051",
        options=[
        ('grpc.max_send_message_length', 1*1024*1024*1024),
        ('grpc.max_receive_message_length', 1*1024*1024*1024),
        ],
    )
    # block until it's actually up (or raise)
    grpc.channel_ready_future(channel).result(timeout=10)
    return job_api_pb2_grpc.JobServiceStub(channel)

# ---- Control logic ----
import time
import socket

@app.post("/experiments", response_model=ExperimentStatus)
async def create_experiment(req: ExperimentStartRequest):
    exp_id = str(uuid.uuid4())

    # --- all numbers as ints, not strings! ---
    server_cfg = {
        "ps_port":         50051,
        "executor_configs":"127.0.0.1:[{}]".format(req.num_executors),
        "num_participants":req.num_clients,
    }
    client_cfg = {
        "ps_ip":     "127.0.0.1",
        "ps_port":   50051,
        "num_participants":  req.num_clients,
        "batch_size": 8
    }

    print(f"Number of executors: {req.num_executors}")

    procs = []

    # 1) start the aggregator
    p_agg = multiprocessing.Process(
        target=_run_aggregator, args=(server_cfg, ), daemon=False
    )
    p_agg.start()
    procs.append(p_agg)

    # 2) wait for gRPC to come up
    for _ in range(20):
        try:
            with socket.create_connection(("127.0.0.1", 50051), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        p_agg.terminate()
        raise RuntimeError("Aggregator gRPC never came up")

    # 3) launch *one* executor that simulates all clients
    p_exec = multiprocessing.Process(
        target=_run_executor, args=(exp_id, 0, client_cfg), daemon=False
    )
    p_exec.start()
    procs.append(p_exec)

    _experiments[exp_id] = {
        "id": exp_id,
        "name": req.name,
        "num_clients": req.num_clients,
        "processes": [p.pid for p in procs],
        "running": True,
    }
    return ExperimentStatus(**_experiments[exp_id])



@app.get("/experiments", response_model=List[ExperimentStatus])
async def list_experiments():
    # return [ExperimentStatus(**exp) for exp in _experiments.values()]
    return list(_experiments.values())


@app.post("/experiments/{exp_id}/stop", response_model=ExperimentStatus)
async def stop_experiment(exp_id: str):
    exp = _experiments.get(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    if not exp["running"]:
        raise HTTPException(400, "Experiment already stopped")

    # kill each PID
    for pid in exp["processes"]:
        subprocess.run(["kill", "-TERM", str(pid)], check=False)

    exp["running"] = False
    return ExperimentStatus(**exp)



@app.get("/experiments/{exp_id}/status")
async def experiment_status(exp_id: str):
    stub = get_stub()
    resp = stub.GetAggregatorStatus(job_api_pb2.StatusRequest())
    return {
      "round": resp.current_round,
      "running": resp.is_running,
      "virtual_clock": resp.global_virtual_clock,
      "sampled_clients": list(resp.sampled_clients),
    }

@app.get("/experiments/{exp_id}/round/{r}/metrics")
async def round_metrics(exp_id: str, r: int):
    stub = get_stub()
    try:
        resp = stub.GetRoundMetrics(job_api_pb2.MetricsRequest(round=r))
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(404, f"No metrics for round {r}")
        else:
            raise HTTPException(500, e.details())

    return {
      "round":     resp.round,
      "test_loss": resp.test_loss,
      "top1":      resp.test_accuracy1,
      "top5":      resp.test_accuracy5,
      "clients": [
        {
          "id":       cm.client_id,
          "loss":     cm.loss,
          "utility":  cm.utility,
          "duration": cm.duration,
          "loss_curve": list(cm.loss_curve),
          "client_eval_local_acc": cm.client_eval_local_acc,
          "client_eval_global_acc": cm.client_eval_local_loss,
          "client_alpha": cm.client_alpha,   
        }
        for cm in resp.clients
      ],
    }



# @app.get("/experiments/{exp_id}/stream")
# async def experiment_stream(exp_id: str):
#     """
#     Server-Sent Events endpoint that streams:
#      - status updates (round, running, clock, sampled_clients)
#      - when a round’s training is actually done, its per-round metrics
#        (test_loss/test_accuracy + per-client loss_curve, etc.)
#     """
#     last_round_emitted = 0
#     stub = get_stub()

#     async def event_generator():
#         nonlocal last_round_emitted

#         while True:
#             # 1) get the latest status (round, is_running, clock, sampled_clients)
#             try:
#                 status = stub.GetAggregatorStatus(job_api_pb2.StatusRequest())
#             except RpcError:
#                 # Aggregator vanished → exit cleanly
#                 break

#             # 2) emit the status frame
#             status_payload = {
#                 "type":            "status",
#                 "round":           status.current_round,
#                 "running":         status.is_running,
#                 "virtual_clock":   status.global_virtual_clock,
#                 "sampled_clients": list(status.sampled_clients),
#             }
#             yield f"data: {json.dumps(status_payload)}\n\n"

#             # 3) if the server has advanced to a new round, try to fetch its metrics
#             r = status.current_round
#             if r >= 1 and (r-1) > last_round_emitted:
#                 try:
#                     print(f"Fetching metrics for round {r}")
#                     m = stub.GetRoundMetrics(job_api_pb2.MetricsRequest(round=r-1))

#                     # print(f"Round {r} m we got: {m}")
#                     for cm in m.clients:
#                         print(f"  Client {cm.client_id} loss: {cm.loss}")

#                 except RpcError:
#                     # either test hasn’t finished or training results not yet in _history[r]
#                     # → wait for the next loop
#                     pass
#                 else:
#                     # only emit *and* advance our counter if there *are* client entries
#                     if len(m.clients) > 0:
#                         last_round_emitted = m.round

#                         clients = [
#                             {
#                                 "id":         cm.client_id,
#                                 "loss":       cm.loss,
#                                 "utility":    cm.utility,
#                                 "duration":   cm.duration,
#                                 "loss_curve": list(cm.loss_curve),
#                                 "client_eval_local_acc": cm.client_eval_local_acc,    # New field
#                                 "client_eval_global_acc": cm.client_eval_global_acc, # New field
#                                 "client_alpha": cm.client_alpha, 
#                             }
#                             for cm in m.clients
#                         ]
#                         metrics_payload = {
#                             "type":      "metrics",
#                             "round":     m.round,
#                             "test_loss": m.test_loss,
#                             "top1":      m.test_accuracy1,
#                             "top5":      m.test_accuracy5,
#                             "clients":   clients,
#                         }
#                         yield f"data: {json.dumps(metrics_payload)}\n\n"

#             # 4) stop if the experiment has ended
#             if not status.is_running:
#                 break

#             # 5) otherwise pause briefly and loop again
#             await asyncio.sleep(1.0)

#     return StreamingResponse(event_generator(), media_type="text/event-stream")




@app.get("/experiments/{exp_id}/stream")
async def experiment_stream(exp_id: str):
    last_round_emitted = 0
    stub = get_stub()

    async def event_generator():
        nonlocal last_round_emitted

        # === NEW: Send history first ===
        if exp_id in metrics_history:
            for cached in metrics_history[exp_id]:
                yield f"data: {json.dumps(cached)}\n\n"
                last_round_emitted = cached["round"]  # so we don't reprocess these later

        while True:
            try:
                status = stub.GetAggregatorStatus(job_api_pb2.StatusRequest())
            except RpcError:
                break

            status_payload = {
                "type":            "status",
                "round":           status.current_round,
                "running":         status.is_running,
                "virtual_clock":   status.global_virtual_clock,
                "sampled_clients": list(status.sampled_clients),
            }
            yield f"data: {json.dumps(status_payload)}\n\n"

            r = status.current_round
            if r >= 1 and (r-1) > last_round_emitted:
                try:
                    m = stub.GetRoundMetrics(job_api_pb2.MetricsRequest(round=r-1))
                except RpcError:
                    pass
                else:
                    if len(m.clients) > 0:
                        last_round_emitted = m.round

                        clients = [
                            {
                                "id":         cm.client_id,
                                "loss":       cm.loss,
                                "utility":    cm.utility,
                                "duration":   cm.duration,
                                "loss_curve": list(cm.loss_curve),
                                "client_eval_local_acc": cm.client_eval_local_acc,
                                "client_eval_global_acc": cm.client_eval_global_acc,
                                "client_alpha": cm.client_alpha, 
                            }
                            for cm in m.clients
                        ]
                        metrics_payload = {
                            "type":      "metrics",
                            "round":     m.round,
                            "test_loss": m.test_loss,
                            "top1":      m.test_accuracy1,
                            "top5":      m.test_accuracy5,
                            "clients":   clients,
                        }

                        # Cache it
                        if exp_id not in metrics_history:
                            metrics_history[exp_id] = []

                        rounds_seen = {m["round"] for m in metrics_history[exp_id]}
                        
                        if m.round not in rounds_seen:
                            metrics_history[exp_id].append(metrics_payload)

                        yield f"data: {json.dumps(metrics_payload)}\n\n"

            if not status.is_running:
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")




@app.get("/health")
async def health():
    return {"status": "ok"}


# To run:
# uvicorn app.main:app --reload --port 8000
