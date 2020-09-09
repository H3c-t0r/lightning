import inspect
from argparse import ArgumentParser, Namespace
from typing import Union, List, Tuple, Any


def from_argparse_args(cls, args: Union[Namespace, ArgumentParser], **kwargs):
    """
    Create an instance from CLI arguments.

    Args:
        args: The parser or namespace to take arguments from. Only known arguments will be
            parsed and passed to the class.
        **kwargs: Additional keyword arguments that may override ones in the parser or namespace.
            These must be valid arguments.

    Example:
        >>> from pytorch_lightning import Trainer
        >>> parser = ArgumentParser(add_help=False)
        >>> parser = Trainer.add_argparse_args(parser)
        >>> parser.add_argument('--my_custom_arg', default='something')  # doctest: +SKIP
        >>> args = Trainer.parse_argparser(parser.parse_args(""))
        >>> trainer = Trainer.from_argparse_args(args, logger=False)
    """
    if isinstance(args, ArgumentParser):
        args = cls.parse_argparser(args)
    params = vars(args)

    # we only want to pass in valid class args, the rest may be user specific
    # we traverse the complete class hierarchy using Python's MRO
    valid_kwargs = inspect.signature(cls.__init__).parameters
    cls_kwargs = dict((name, params[name]) for name in valid_kwargs if name in params)
    cls_kwargs.update(**kwargs)

    return cls(**cls_kwargs)


def parse_argparser(cls, arg_parser: Union[ArgumentParser, Namespace]) -> Namespace:
    """Parse CLI arguments, required for custom bool types."""
    args = arg_parser.parse_args() if isinstance(arg_parser, ArgumentParser) else arg_parser

    types_default = {
        arg: (arg_types, arg_default) for arg, arg_types, arg_default in get_init_arguments_and_types(cls)
    }

    modified_args = {}
    for k, v in vars(args).items():
        if k in types_default and v is None:
            # We need to figure out if the None is due to using nargs="?" or if it comes from the default value
            arg_types, arg_default = types_default[k]
            if bool in arg_types and isinstance(arg_default, bool):
                # Value has been passed as a flag => It is currently None, so we need to set it to True
                # We always set to True, regardless of the default value.
                # Users must pass False directly, but when passing nothing True is assumed.
                # i.e. the only way to disable somthing that defaults to True is to use the long form:
                # "--a_default_true_arg False" becomes False, while "--a_default_false_arg" becomes None,
                # which then becomes True here.

                v = True

        modified_args[k] = v
    return Namespace(**modified_args)


def get_init_arguments_and_types(cls) -> List[Tuple[str, Tuple, Any]]:
    r"""Scans the class signature and returns argument names, types and default values.

    Returns:
        List with tuples of 3 values:
        (argument name, set with argument types, argument default value).

    Examples:
        >>> from pytorch_lightning import Trainer
        >>> args = get_init_arguments_and_types(Trainer)
        >>> import pprint
        >>> pprint.pprint(sorted(args))  # doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
        [('accumulate_grad_batches',
          (<class 'int'>, typing.Dict[int, int], typing.List[list]),
          1),
         ...
         ('callbacks',
          (typing.List[pytorch_lightning.callbacks.base.Callback],
           <class 'NoneType'>),
           None),
         ('check_val_every_n_epoch', (<class 'int'>,), 1),
         ...
         ('max_epochs', (<class 'int'>,), 1000),
         ...
         ('precision', (<class 'int'>,), 32),
         ('prepare_data_per_node', (<class 'bool'>,), True),
         ('process_position', (<class 'int'>,), 0),
         ('profiler',
          (<class 'pytorch_lightning.profiler.profilers.BaseProfiler'>,
           <class 'bool'>,
           <class 'NoneType'>),
          None),
         ...
    """
    cls_default_params = inspect.signature(cls.__init__).parameters
    name_type_default = []
    for arg in cls_default_params:
        arg_type = cls_default_params[arg].annotation
        arg_default = cls_default_params[arg].default
        try:
            arg_types = tuple(arg_type.__args__)
        except AttributeError:
            arg_types = (arg_type,)

        name_type_default.append((arg, arg_types, arg_default))

    return name_type_default
