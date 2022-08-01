"""Give me (converted) GCM data, I give you TF inputs"""

import numpy as np
import xarray as xr
from tensorflow import keras

from .utils import split_dataset, XNormalizer

class KerasFeeder():
    """Do the following

        1. Subset features, label, and any masks from dataset
        2.
    """

    horizontal_dim      = 'x'
    horizontal_index    = 'ix'
    vertical_dim        = 'z'
    sample_dim          = 'sample'

    load_into_memory    = False
    normalize_data      = True
    training_fraction   = 0.8

    features            = None
    labels              = None
    inputs              = None

    @property
    def horizontal_stack(self):
        return {self.horizontal_dim : ('lat', 'lon')}


    @property
    def sample_stack(self):
        return {self.sample_dim : ('member', self.horizontal_index)}


    @property
    def vertical_stack(self):
        return {self.vertical_dim : ('alev', 'olev')}


    @property
    def dim_order(self):
        return (self.sample_dim, 'olev', 'alev')


    @property
    def x_training(self):
        if self.features is not None:
            return {k:self.features['training'][k].data for k in self.feature_names}
        else:
            return None


    @property
    def x_testing(self):
        if self.features is not None:
            return {k:self.features['testing'][k].data for k in self.feature_names}
        else:
            return None


    def __init__(self, feature_names, label_name, mask_name=None, **kwargs):

        self.feature_names  = feature_names
        self.label_name     = label_name
        self.mask_name      = mask_name

        # Change any attribute defaults provided in kwargs
        for key, val in kwargs.items():
            setattr(self, key, val)


    def __call__(self, xds):

        xds = self.subset_dataset(xds)

        if self.load_into_memory:
            xds = xds.load();

        xds = self.stack_horizontal(xds)

        xds = self.remove_nans(xds)

        # This step is a little hacky right now
        xds[self.label_name] = self.broadcast_for_label(xds, dimname="member")

        xds = self.stack_sample(xds)

        xds = self.reorder_dims(xds)

        self.set_features_and_labels(xds)

        if self.normalize_data:
            self.features = self.normalize(self.features, self.sample_dim)

        self.labels = self.stack_vertical(self.labels)

        self.set_keras_inputs(self.features["training"])


    def __str__(self):
        fstatus = "unset" if self.features is None else "set"
        lstatus = "unset" if self.labels is None else "set"
        istatus = "unset" if self.inputs is None else "set"
        mystr = \
                f"KerasFeeder:\n\n"+\
                f"    Features:\n"+\
                f"        {', '.join(self.feature_names)}\n"+\
                f"        status = {fstatus}\n\n"+\
                f"    Labels:\n"+\
                f"        {self.label_name}\n"+\
                f"        status = {lstatus}\n\n"+\
                f"    Inputs:\n"+\
                f"        {str(self.inputs)}\n"+\
                f"        status = {istatus}\n\n"+\
                f" --- \n"+\
                f"    {'Training Fraction':<24s}: {self.training_fraction}\n"+\
                f"    {'Normalize Data':<24s}: {self.normalize_data}\n"+\
                f"    {'Load into Memory':<24s}: {self.load_into_memory}\n"

        return mystr


    def __repr__(self):
        return self.__str__()


    def stack_horizontal(self, xds):
        """Stack horizontal dimensions into 1, then replace this dimension with a logical index so that future operations don't complain. But, keep the stacked index so we can always map from logical index back to the original unstacked coordinates (e.g. ix -> x -> (lat, lon) ).

        Args:
            xds (:obj:`xarray.Dataset`): dataset to stack

        Returns:
            stacked (:obj:`xarray.Dataset`): with stacked horizontal coordinates
        """
        stacked = xds.stack(self.horizontal_stack)
        latlon = str(self.horizontal_stack[self.horizontal_dim])
        stacked[self.horizontal_dim].attrs = {'description': 'mapping from single coordinate to {latlon}'}

        hd = stacked[self.horizontal_dim]
        stacked[self.horizontal_index] = xr.DataArray(np.arange(len(hd)),
                                                      coords=hd.coords,
                                                      dims=hd.dims,
                                                      attrs={'description': f'logical index corresponding to stacked/MultiIndex coordinate "{self.horizontal_dim}"'})

        stacked = stacked.swap_dims({self.horizontal_dim : self.horizontal_index})
        return stacked


    def get_mask(self, xds):
        """Make a mask based on :attr:`mask_name` and any NaNs in label array. Return a mask that is only True where label array is not NaN and xds[mask_name] == True.

        Args:
            xds (:obj:`xarray.Dataset`): with features, label, and maybe some masks

        Returns:
            mask (:obj:`xarray.DataArray`): one mask to rule them all
        """

        mask = ~np.isnan(xds[self.label_name]).any(["alev", "olev"])
        mask = mask & xds[self.mask_name]
        return mask


    def remove_nans(self, xds):
        """Get and apply mask obtained :meth:`get_mask`. Points where ``mask == False`` are removed from all arrays in ``xds``.
        """
        mask = self.get_mask(xds)
        return xds.where(mask, drop=True)


    def set_features_and_labels(self, xds, random_seed=None):
        """Split dataset into training and testing datasets, and separate features and labels.
        TODO: separate validation as well.
        """
        train_ds, test_ds = split_dataset(xds,
                                          dim=self.sample_dim,
                                          fraction=self.training_fraction,
                                          random_seed=random_seed)

        features = {
                "training"  : {k : train_ds[k] for k in self.feature_names},
                "testing"   : {k : test_ds[k] for k in self.feature_names}}
        labels = {
                "training"  : train_ds[self.label_name],
                "testing"  : test_ds[self.label_name]}

        self.features = features
        self.labels = labels


    @staticmethod
    def normalize(features, dims):
        """Apply normalization to features based on training data, return normalized trainer and tester as dictionaries.

        Args:
            features (dict): with keys "training" and "testing", each mapping to dictionaries with training and testing features
            dims (str or list of str): dimension to normalize along

        Returns:
            normalized_features (dict): same format as input, data is normalized based on training data
        """

        ntrainer = {}
        ntester = {}
        for key in features["training"].keys():
            norm = XNormalizer(dims=dims)
            norm.adapt(features["training"][key])

            ntrainer[key] = norm(features["training"][key])
            ntester[key] = norm(features["testing"][key])

        normalized_features = {
                "training"  : ntrainer,
                "testing"   : ntester}

        return normalized_features


    def stack_vertical(self, labels):
        """Stack the label data array for each training, testing phase

        Args:
            labels (dict): containing :obj:`xarray.DataArray` objects for training and testing

        Returns:
            stacked (dict): each array now has the vertical dimension stacked according to :attr:`vertical_stack`
        """

        stacked = {}
        for key, val in labels.items():
            stacked[key] = val.stack(self.vertical_stack)

        return stacked


    def set_keras_inputs(self, xds):
        """Produce a list of :obj:`keras.Input` objects, corresponding to each feature

        Args:
            xds (dict or :obj:`xarray.Dataset`): keys of this object need to map to feature arrays, just to get coordinate information

        Sets Attributes:
            inputs (list of :obj:`keras.Input`): which can be supplied to a :obj:`keras.Model`
        """

        inputs = []
        for key in self.feature_names:

            # Get the shape of the dimensions, other than :attr:`sample_dim`
            dims = tuple(d for d in xds[key].dims if d != self.sample_dim)
            shape = tuple(len(xds[key][d]) for d in dims)
            shape = (1,) if shape == () else shape

            inputs.append(keras.Input(shape=shape, name=key))

        self.inputs = inputs


    # Not really clear if these one-liners need their own methods ... oh well
    def subset_dataset(self, xds):
        return xds[self.feature_names + [self.label_name, self.mask_name]]


    def stack_sample(self, xds):
        return xds.stack(self.sample_stack)


    def reorder_dims(self, xds):
        return xds.transpose(*self.dim_order)


    def broadcast_for_label(self, xds, dimname):
        return xds[self.label_name].broadcast_like(xds[dimname])