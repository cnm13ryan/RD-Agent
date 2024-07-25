"""
This is a class that try to store/resume/traceback the workflow session


Postscripts:
- Originally, I want to implement it in a more general way with python generator.
  However, Python generator is not picklable (dill does not support pickle as well)

"""
from pathlib import Path
import pickle
from tqdm.auto import tqdm


from collections import defaultdict
from dataclasses import dataclass, field
import datetime
from typing import Callable
from rdagent.log import rdagent_logger as logger


class LoopMeta(type):

    @staticmethod
    def _get_steps(bases):
        """
        get all the `steps` of base classes and combine them to a single one.
        """
        steps = []
        for base in bases:
            steps.extend(LoopMeta._get_steps(base.__bases__) + getattr(base,"steps", []))
        return steps

    def __new__(cls, clsname, bases, attrs):
        # move custommized steps into steps
        steps = LoopMeta._get_steps(bases)  # all the base classes of parents
        for name, attr in attrs.items():
            if not name.startswith("__") and isinstance(attr, Callable):
                steps.append(name)
        attrs["steps"] = steps
        return super().__new__(cls, clsname, bases, attrs)


@dataclass
class LoopTrace:
    start: datetime.datetime  # the start time of the trace
    end: datetime.datetime  # the end time of the trace
    # TODO: more information about the trace


class LoopBase:
    steps: list[Callable]  # a list of steps to work on
    loop_trace: dict[int, list[LoopTrace]]

    skip_loop_error: tuple[Exception]  = field(default_factory=tuple)  # you can define a list of error that will skip current loop

    def __init__(self):
        self.loop_idx = 0 # current loop index
        self.step_idx = 0 # the index of next step to be run
        self.loop_prev_out = {} # the step results of current loop
        self.loop_trace = defaultdict(list[LoopTrace])  # the key is the number of loop
        self.session_folder = logger.log_trace_path / "__session__"

    def run(self, step_n: int | None = None):
        """

        Parameters
        ----------
        step_n : int | None
            How many steps to run;
            `None` indicates to run forever until error or KeyboardInterrupt
        """
        with tqdm(total=len(self.steps), desc="Workflow Progress", unit="step") as pbar:
            while True:
                if step_n is not None:
                    if step_n <= 0:
                        break
                    step_n -= 1

                li, si = self.loop_idx, self.step_idx

                start = datetime.datetime.now(datetime.timezone.utc)

                name = self.steps[si]
                func = getattr(self, name)
                try:
                    self.loop_prev_out[name] = func(self.loop_prev_out)
                    # TODO: Fix the error logger.exception(f"Skip loop {li} due to {e}")
                except self.skip_loop_error as e:
                    logger.warning(f"Skip loop {li} due to {e}")
                    self.loop_idx += 1
                    self.step_index = 0
                    continue

                end = datetime.datetime.now(datetime.timezone.utc)

                self.loop_trace[li].append(LoopTrace(start, end))

                # Update tqdm progress bar
                pbar.set_postfix(loop_index=li, step_index=si, step_name=name)
                pbar.update(1)

                # index increase and save session
                self.step_idx = (self.step_idx + 1) % len(self.steps)
                if self.step_idx == 0:  # reset to step 0 in next round
                    self.loop_idx += 1
                    self.loop_prev_out = {}
                    pbar.reset()  # reset the progress bar for the next loop

                self.dump(self.session_folder / f"{li}" / f"{si}_{name}")  # save a snapshot after the session

    def dump(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: str | Path):
        path = Path(path)
        with path.open("rb") as f:
            session = pickle.load(f)
        logger.set_trace_path(session.session_folder.parent)
        
        max_loop = max(session.loop_trace.keys())
        logger.storage.truncate(time=session.loop_trace[max_loop][-1].end)
        return session