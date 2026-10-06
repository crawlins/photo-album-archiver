"""Album page processing: several phone photos of a page -> one clean page image."""

__version__ = "0.2.0"

from .glare import GlareParams, detect_glare  # noqa: E402
from .pipeline import PageOptions, PageReport, PageResult, process_page  # noqa: E402
from .regions import QualityParams  # noqa: E402
from .printimage import PrintOptions, make_print_image  # noqa: E402
from .printsize import PageSize, parse_page_size  # noqa: E402
from .album import AlbumOptions, process_album  # noqa: E402

__all__ = [
    "AlbumOptions",
    "GlareParams",
    "QualityParams",
    "PageOptions",
    "PageReport",
    "PageResult",
    "PageSize",
    "PrintOptions",
    "detect_glare",
    "make_print_image",
    "parse_page_size",
    "process_album",
    "process_page",
]
