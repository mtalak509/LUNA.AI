from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    """Version from the installed package metadata — the single source of truth (pyproject).

    Fallback for a checkout that was not installed with `pip install -e .`.
    """
    try:
        return version("luna-agent")
    except PackageNotFoundError:
        return "0.0.0+dev"


def print_banner() -> str:
    """Print the banner."""
    banner = """
    ██╗     ██╗   ██╗███╗   ██╗ █████╗          
    ██║     ██║   ██║████╗  ██║██╔══██╗         
    ██║     ██║   ██║██╔██╗ ██║███████║         
    ██║     ██║   ██║██║╚██╗██║██╔══██║         
    ███████╗╚██████╔╝██║ ╚████║██║  ██║         
    ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝         
                                                
    █████╗  ██████╗ ███████╗███╗   ██╗████████╗
    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
    ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   
    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   
    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   
    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
    """
    return f"{banner}\n    v{get_version()}\n"
