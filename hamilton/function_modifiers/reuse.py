import inspect
from types import ModuleType
from typing import Any, Callable, Collection, Dict, List, Tuple, Type, Union

# Copied this over from function_graph
# TODO -- determine the best place to put this code
from hamilton import node
from hamilton.function_modifiers import base, dependencies


def find_functions(function_module: ModuleType) -> List[Tuple[str, Callable]]:
    """Function to determine the set of functions we want to build a graph from.

    This iterates through the function module and grabs all function definitions.
    :return: list of tuples of (func_name, function).
    """

    # kind of hacky for now but it will work
    def is_submodule(child: ModuleType, parent: ModuleType):
        return parent.__name__ in child.__name__

    def valid_fn(fn):
        return (
            inspect.isfunction(fn)
            and not fn.__name__.startswith("_")
            and is_submodule(inspect.getmodule(fn), function_module)
        )

    return [f for f in inspect.getmembers(function_module, predicate=valid_fn)]


class MultiOutput:
    def __init__(self, **mapping: Type):
        self._mapping = mapping

    @property
    def mapping(self) -> Dict[str, Type]:
        return self._mapping


def get_namespaces(node_: node.Node) -> List[str]:
    *namespaces, _ = node_.name.split(".")
    return namespaces


def assign_namespace(node_name: str, namespace: str) -> str:
    return f"{namespace}.{node_name}"


def derive_type(dependency: dependencies.LiteralDependency):
    """Quick hack to derive the type of a static dependency.
    We might want to consider the type provided by the function that needs it.
    Or we can use the subclass checker/whatnot in function_graph
    (althoyugh we'll want to move it out)

    :param dependency: Dependency on which
    :return: The type of the dependency
    """
    return type(dependency.value)


def create_identity_node(from_: str, typ: Type[Type], name: str):
    """Creates an identity node -- this passes through the exact
    value returned by the upstream node.

    :param from_: Source node
    :param typ: Type of the input node
    :param name: Name of the final node to create
    :return: A node that simply copies the source node
    """

    def identity(**kwargs):
        return list(kwargs.values())[0]  # Maybe come up with a better way to do this

    return node.Node(
        name=name,
        typ=typ,
        doc_string="",
        callabl=identity,
        input_types={from_: typ},
        # TODO -- add tags?
    )


def extract_all_known_types(nodes: Collection[node.Node]) -> Dict[str, Type[Type]]:
    """Extracts all known types from a set of nodes given the dependencies.
    We have to do this as we don't know the dependency types at compile-time of
    upstream nodes. That said, this is only used for guessing dependency types of
    identity nodes. In which case, we probably want some sort of sentinel "pass-through"
    dependency type that handles this better. But, for now, we'll derive it from the
    dependencies we've seen.

    :param nodes: nodes to look through for dependencies
    :return: A dictionary of all known types.
    """
    observed_types = {}
    for node_ in nodes:
        for dep_name, (type_, _) in node_.input_types.items():
            observed_types[dep_name] = type_
    return observed_types


def create_static_node(typ: Type, name: str, value: Any) -> node.Node:
    """Utility function to create a static node -- this helps us bridge nodes together.

    :param typ: Type of the node to create
    :param name: Name of the node to create
    :param value: Value that the node's function always returns
    :return: The instantiated static node
    """

    def node_fn(_value=value):
        return _value

    return node.Node(name=name, typ=typ, callabl=node_fn, input_types={})


class reuse_subdag(base.NodeCreator):
    def __init__(
        self,
        with_inputs: Dict[str, dependencies.ParametrizedDependency],
        namespace: str,
        outputs: Dict[str, str],
        with_config: Dict[str, Any],
        load_from: Union[Collection[ModuleType], Collection[Callable]],
    ):
        """Initializes a replay decorator. This decorator replays a subdag with a specified configuration.

        :param load_from: The functions that will be used to generate this subDAG
        :param namespace: Namespace with which to prefict nodes
        :param with_inputs: Parameterized dependencies to inject into all sources of this subDAG.
        This should *not* be an intermediate node in the subDAG.
        :param outputs: A dictionary of original node name -> output node name that forms the output of this DAG.
        :param with_config: A configuration dictionary for *just* this subDAG. Note that this passed in value takes precedence.
        """
        self.subdag_functions = reuse_subdag.collect_functions(load_from)
        self.with_inputs = with_inputs
        self.namespace = namespace
        self.outputs = outputs
        self.with_config = with_config

    @staticmethod
    def collect_functions(
        load_from: Union[Collection[ModuleType], Collection[Callable]]
    ) -> List[Callable]:
        """Utility function to collect functions from a list of callables/modules.

        :param load_from: A list of callables or modules to load from
        :return: a list of callables to use to create a DAG.
        """
        if len(load_from) == 0:
            raise ValueError(f"No functions were passed to {reuse_subdag.__name__}(load_from=...)")
        out = []
        for item in load_from:
            if isinstance(item, Callable):
                out.append(item)
            out.extend([function for _, function in find_functions(function_module=item)])
        return out

    def _collect_nodes(self, original_config: Dict[str, Any]):
        combined_config = dict(original_config, **self.with_config)
        nodes = []
        for fn in self.subdag_functions:
            nodes.extend(base.resolve_nodes(fn, combined_config))
        return nodes

    def _create_additional_static_nodes(
        self, nodes: Collection[node.Node]
    ) -> Collection[node.Node]:
        # These already have the namespace on them
        # This allows us to inject values into the replayed subdag
        node_types = extract_all_known_types(nodes)
        out = []
        for key, value in self.with_inputs.items():
            # TODO -- fix type derivation. Currently we don't use the specified type as we don't really know what it should be...
            new_node_name = assign_namespace(key, self.namespace)
            if value.get_dependency_type() == dependencies.ParametrizedDependencySource.LITERAL:
                out.append(
                    create_static_node(
                        typ=derive_type(value), name=new_node_name, value=value.value
                    )
                )
            elif value.get_dependency_type() == dependencies.ParametrizedDependencySource.UPSTREAM:
                out.append(
                    create_identity_node(
                        from_=value.source, typ=node_types[new_node_name], name=new_node_name
                    )
                )
        return out

    def _add_namespace(self, nodes: List[node.Node]) -> List[node.Node]:
        """Utility function to add a namespace to nodes. Note that this is

        :param nodes:
        :return:
        """
        already_namespaced_nodes = []
        new_nodes = []
        new_name_map = {}
        # First pass we validate + collect names so we can alter dependencies
        for node_ in nodes:
            new_name = assign_namespace(node_.name, self.namespace)
            new_name_map[node_.name] = new_name
            current_node_namespaces = get_namespaces(node_)
            if current_node_namespaces:
                already_namespaced_nodes.append(node_)
        for dep, value in self.with_inputs.items():
            # We create nodes for both namespace assignment and source assignment
            # Why? Cause we need unique parameter names, and with source() some can share params
            new_name_map[dep] = assign_namespace(dep, self.namespace)
        if already_namespaced_nodes:
            raise ValueError(
                f"The following nodes are already namespaced: {already_namespaced_nodes}. "
                f"We currently do not allow for multiple namespaces (E.G. layered subDAGs)."
            )
        # Reassign sources
        for node_ in nodes:
            new_name = new_name_map[node_.name]
            kwarg_mapping = {
                (new_name_map[key] if key in new_name_map else key): key
                for key in node_.input_types
            }

            # Map of argumnet in function to source, can't be the other way
            # around as sources can potentially serve multiple destinations (with the source()) decorator
            def fn(
                _callabl=node_.callable,
                _kwarg_mapping=dict(kwarg_mapping),
                _new_name=new_name,
                _new_name_map=dict(new_name_map),
                **kwargs,
            ):
                new_kwargs = {_kwarg_mapping[kwarg]: value for kwarg, value in kwargs.items()}
                return _callabl(**new_kwargs)

            new_input_types = {
                dep: node_.input_types[original_dep] for dep, original_dep in kwarg_mapping.items()
            }

            new_nodes.append(
                node_.copy_with(input_types=new_input_types, name=new_name, callabl=fn)
            )
        return new_nodes

    def _add_output_nodes(self, nodes: List[node.Node]) -> List[node.Node]:
        nodes_by_name = {node_.name: node_ for node_ in nodes}
        new_nodes = []
        for from_node, to_node in self.outputs.items():
            from_node_namespaced = assign_namespace(from_node, self.namespace)
            new_nodes.append(
                create_identity_node(
                    from_=from_node_namespaced,
                    name=to_node,
                    typ=nodes_by_name[from_node_namespaced].type,
                )
            )
        return nodes + new_nodes

    def generate_nodes(self, fn: Callable, configuration: Dict[str, Any]) -> Collection[node.Node]:
        # Resolve all nodes from passed in functions
        nodes = self._collect_nodes(original_config=configuration)
        # Rename them all to have the right namespace
        nodes = self._add_namespace(nodes)
        # Add output nodes (these are identity nodes that assign the desired name
        nodes = self._add_output_nodes(nodes)
        # Create any static input nodes we need
        nodes += self._create_additional_static_nodes(nodes)
        return nodes

    def _validate_function_output(self, fn: Callable):
        """Validates that the function outputs a MultiNodeOutput that contains all the nodes that are mapped in output.

        :param fn: Function to inspect.
        :raises InvalidDecoratorException: If the function does not supply the right outputs.
        """
        return_type = inspect.signature(fn).return_annotation
        if not isinstance(return_type, MultiOutput):
            raise base.InvalidDecoratorException(
                f"Output of function {fn.__name__} must be 'MultiNodeOutput'. Instead got {return_type}"
            )
        output_mapping_nodes = set(self.outputs.values())
        type_spec_keys = set(return_type.mapping.keys())
        if len(output_mapping_nodes.symmetric_difference(type_spec_keys)) != 0:
            raise base.InvalidDecoratorException(
                "The mapping of outputs to types "
                "must exactly match the assignment of outputs in a "
                "subDAG to corresponding names in the overall DAG."
            )

    def _validate_parameterization(self):
        invalid_values = []
        for key, value in self.with_inputs.items():
            if not isinstance(value, dependencies.ParametrizedDependency):
                invalid_values.append(value)
        if invalid_values:
            raise ValueError(
                f"Parameterization using the following values is not permitted -- "
                f"must be either source() or value(): {invalid_values}"
            )

    def validate(self, fn):
        """Validates everything we can before the

        :param fn:
        :return:
        """

        self._validate_function_output(fn)
        self._validate_parameterization()