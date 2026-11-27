from django.contrib import admin

from .models import ExecutionStep, Workflow, WorkflowConnection, WorkflowExecution, WorkflowNode, WorkflowTrigger

admin.site.register(Workflow)
admin.site.register(WorkflowNode)
admin.site.register(WorkflowConnection)
admin.site.register(WorkflowTrigger)
admin.site.register(WorkflowExecution)
admin.site.register(ExecutionStep)
