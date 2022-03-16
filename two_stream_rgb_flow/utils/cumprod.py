from chainer.backends import cuda
from chainer import function_node
from chainer.functions.array import flip
from chainer.utils import type_check


class Cumprod(function_node.FunctionNode):
    """Cumulative product of array elements over a given axis."""
    def __init__(self, axis=None):
        if isinstance(axis, int) or axis is None:
            self.axis = axis
        else:
            raise TypeError('axis must be int or None')

    def check_type_forward(self, in_types):
        type_check.expect(in_types.size()==1,
                          in_types[0].dtype.kind == 'f')
        if self.axis is not None:
            if self.axis >= 0:
                type_check.expect(self.axis < in_types[0].ndim)
            else:
                type_check.expect(-self.axis -1 < in_types[0].ndim)

    def forward(self, inputs):
        x, = inputs
        self._in_shape = x.shape
        xp = cuda.get_array_module(x)
        return xp.cumprod(x, axis=self.axis)

    def backward(self, indexes, grad_outputs):
        gy = grad_outputs[0]
        axis =self.axis
        if axis is not None:
            gx = flip.flip(cumprod(flip.flip(gy, axis), axis), axis)
        else:
            gx = flip.flip(cumprod(flip.flip(gy, 0), 0), 0)
            gx = gx.reshape(self._in_shape)
        return gx,

def cumprod(x, axis=None):
    return Cumprod(axis).apply((x,))[0]