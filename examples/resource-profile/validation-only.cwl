cwlVersion: v1.2
class: CommandLineTool
label: ResourceRequirement and ToolTimeLimit syntax fixture
doc: >-
  Schema-validation fixture only. The numbers demonstrate CWL syntax and are
  not resource estimates or permission to execute an experiment.
baseCommand: [python3, -c, "pass"]
inputs: []
outputs: []
requirements:
  ResourceRequirement:
    coresMin: 1
    coresMax: 1
    ramMin: 32
    ramMax: 32
    tmpdirMin: 1
    tmpdirMax: 1
    outdirMin: 1
    outdirMax: 1
  ToolTimeLimit:
    timelimit: 30
