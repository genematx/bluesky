import os
import time as ttime
from .core import CallbackBase
import numpy as np
import doct


class LiveImage(CallbackBase):
    """
    Stream 2D images in a cross-section viewer.

    Parameters
    ----------
    field : string
        name of data field in an Event
    """
    def __init__(self, field):
        from xray_vision.backend.mpl.cross_section_2d import CrossSection
        import matplotlib.pyplot as plt
        super().__init__()
        self.field = field
        fig = plt.figure()
        self.cs = CrossSection(fig)
        self.cs._fig.show()

    def event(self, doc):
        import filestore.api as fsapi
        uid = doc['data'][self.field]
        data = fsapi.retrieve(uid)
        self.cs.update_image(data)
        self.cs._fig.canvas.draw_idle()


def post_run(callback, db=None):
    """Trigger a callback to process all the Documents from a run at the end.

    This function does not receive the Document stream during collection.
    It retrieves the complete set of Documents from the DataBroker after
    collection is complete.

    Parameters
    ----------
    callback : callable
        Expected signature ::

            def func(doc_name, doc):
                pass

    db : Broker, optional
        The databroker instance to use, if not provided use databroker
        singleton

    Returns
    -------
    func : function
        a function that acepts a RunStop Document

    Examples
    --------
    Print a table with full (lossless) result set at the end of a run.

    >>> s = AbsScanPlan(motor, [det1], [1,2,3])
    >>> table = LiveTable(['det1', 'motor'])
    >>> RE(s, {'stop': post_run(table)})
    +------------+-------------------+----------------+----------------+
    |   seq_num  |             time  |          det1  |         motor  |
    +------------+-------------------+----------------+----------------+
    |         3  |  14:02:32.218348  |          5.00  |          3.00  |
    |         2  |  14:02:32.158503  |          5.00  |          2.00  |
    |         1  |  14:02:32.099807  |          5.00  |          1.00  |
    +------------+-------------------+----------------+----------------+

    """

    if db is None:
        from databroker import DataBroker as db

    def f(name, stop_doc):
        if name != 'stop':
            return
        uid = stop_doc['run_start']
        return db.process(db[uid], callback)
    return f


def make_restreamer(callback, db=None):
    """
    Run a callback whenever a uid is updated.

    Parameters
    ----------
    callback : callable
        expected signature is `f(name, doc)`

    db : Broker, optional
        The databroker instance to use, if not provided use databroker
        singleton


    Example
    -------
    Run a callback whenever a uid is updated.

    >>> def f(name, doc):
    ...     # do stuff
    ...
    >>> g = make_restreamer(f)

    To use this `ophyd.callbacks.LastUidSignal`:

    >>> last_uid_signal.subscribe(g)
    """
    from databroker import process

    if db is None:
        from databroker import DataBroker as db

    def cb(value, **kwargs):
        return process(db[value], callback)
    return cb


def verify_files_saved(name, doc, db=None):
    "This is a brute-force approach. We retrieve all the data."
    if db is None:
        from databroker import DataBroker as db

    ttime.sleep(0.1)  # Wati for data to be saved.
    if name != 'stop':
        return
    print("  Verifying that all the run's Documents were saved...")
    try:
        header = db[doc['run_start']]
    except Exception as e:
        print("  Verification Failed! Error: {0}".format(e))
        return
    else:
        print('\x1b[1A\u2713')  # print a checkmark on the previous line
    print("  Verifying that all externally-stored files are accessible...")
    try:
        list(db.get_events(header, fill=True))
    except Exception as e:
        print("  Verification Failed! Error: {0}".format(e))
    else:
        print('\x1b[1A\u2713')  # print a checkmark on the previous line


class LiveTiffExporter(CallbackBase):
    """
    Save TIFF files.

    Incorporate metadata and data from individual data points in the filenames.

    Parameters
    ----------
    field : str
        a data key, e.g., 'image'
    template : str
        A templated file path, where curly brackets will be filled in with
        the attributes of 'start', 'event', and (for image stacks) 'i',
        a sequential number.
        e.g., "dir/scan{start.scan_id}_by_{start.experimenter}_{i}.tiff"
    dryrun : bool
        default to False; if True, do not write any files
    overwrite : bool
        default to False, raising an OSError if file exists
    db : Broker, optional
        The databroker instance to use, if not provided use databroker
        singleton

    Attributes
    ----------
    filenames : list of filenames written in ongoing or most recent run
    """
    def __init__(self, field, template, dryrun=False, overwrite=False,
                 db=None):
        try:
            import tifffile
        except ImportError:
            print("Tifffile is required by this callback. Please install"
                  "tifffile and then try again."
                  "\n\n\tpip install tifffile\n\nor\n\n\tconda install "
                  "tifffile")
            raise
        else:
            # stash a reference so the module is accessible in self._save_image
            self._tifffile = tifffile

        if db is None:
            from databroker import DataBroker as db

        self.db = db

        self.field = field
        self.template = template
        self.dryrun = dryrun
        self.overwrite = overwrite
        self.filenames = []
        self._start = None

    def _save_image(self, image, filename):
        if not self.overwrite:
            if os.path.isfile(filename):
                raise OSError("There is already a file at {}. Delete "
                              "it and try again.".format(filename))
        if not self.dryrun:
            self._tifffile.imsave(filename, np.asarray(image))
        self.filenames.append(filename)

    def start(self, doc):
        self.filenames = []
        # Convert doc from dict into dottable dict, more convenient
        # in Python format strings: doc.key == doc['key']
        self._start = doct.Document('start', doc)

    def event(self, doc):
        # Convert doc from dict into dottable dict, more convenient
        # in Python format strings: doc.key == doc['key']
        doc = doct.Document('event', doc)
        if self.field not in doc['data']:
            return
        self.db.fill_event(doc)  # modifies in place
        image = np.asarray(doc['data'][self.field])
        if image.ndim == 2:
            filename = self.template.format(start=self._start, event=doc)
            self._save_image(image, filename)
        if image.ndim == 3:
            for i, plane in enumerate(image):
                filename = self.template.format(i=i, start=self._start,
                                                event=doc)
                self._save_image(plane, filename)
        # RunEngine will ignore this return value, but it
        # might be handy for interactive use.
        return filename

    def stop(self, doc):
        self._start = None
        self.filenames = []
