# app.py
import lightning as L


class TrainComponent(L.LightningWork):
    def run(self, x):
        print(f'train a model on {x}')

class AnalyzeComponent(L.LightningWork):
    def run(self, x):
        print(f'analyze model on {x}')

class WorkflowOrchestrator(L.LightningFlow):
    def __init__(self) -> None:
        super().__init__()
        self.train = TrainComponent(cloud_compute=L.CloudCompute('cpu'))
        self.analyze = AnalyzeComponent(cloud_compute=L.CloudCompute('gpu'))

    def run(self):
        self.train.run("CPU machine 1")
        self.analyze.run("GPU machine 2")

app = L.LightningApp(WorkflowOrchestrator())
