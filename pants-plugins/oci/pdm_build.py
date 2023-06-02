def pdm_build_initialize(context):
    context.build_dir = context.root / ".build"

def pdm_build_clean(context):
    context.build_dir = context.root / ".build"