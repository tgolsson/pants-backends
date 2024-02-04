pex_binary(
    name="gen",
    entry_point="gen.py",
)

python_sources()

adhoc_tool(
    name="gen_config",
    runnable=":gen",
    args=["out.yaml"],
    execution_dependencies=[""],
    output_files=["out.yaml"],
)

kubeconfig(
    name="config",
    from_generator=[":gen_config"]
)
