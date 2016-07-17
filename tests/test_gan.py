# Copyright 2015 Leon Sixt
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from conftest import visual_debug, TEST_OUTPUT_DIR
import os
import keras
import theano
import keras.initializations
import keras.backend as K
from keras.layers.convolutional import Convolution2D
from keras.layers.core import Dense, Flatten
from keras.layers.advanced_activations import LeakyReLU
from keras.optimizers import Adam
from keras.engine.topology import Input, merge, Container
from keras.engine.training import Model
from keras.utils.layer_utils import layer_from_config
import math
import pytest
import numpy as np

from diktyo.gan import GAN
from diktyo.layers.core import Split
from diktyo.util import sequential, concat


def sample_circle(nb_samples):
    center = (0.2, 0.2)
    r = np.random.normal(0.5, 0.1, (nb_samples, ))
    angle = np.random.uniform(0, 2*math.pi, (nb_samples,))
    X = np.zeros((nb_samples, 2))
    X[:, 0] = r*np.cos(angle) + center[0]
    X[:, 1] = r*np.sin(angle) + center[1]
    return X


class Plotter(keras.callbacks.Callback):
    def __init__(self, X, outdir):
        super().__init__()
        self.X = X
        self.outdir = outdir
        os.makedirs(outdir, exist_ok=True)

    def on_epoch_begin(self, epoch, logs={}):
        if epoch == 0:
            self._plot("on_begin_0.png")

    def on_epoch_end(self, epoch, logs={}):
        self._plot("on_end_{}.png".format(epoch))

    def _plot(self, outname):
        import matplotlib.pyplot as plt
        ys = []
        for i in range(32):
            ys.append(self.model.generate())
        Y = np.concatenate(ys)
        fig = plt.figure()
        plt.ylim(-1, 1.5)
        plt.xlim(-1, 1.5)
        plt.scatter(self.X[:, 0], self.X[:, 1], marker='.', c='b', alpha=0.2)
        plt.scatter(Y[:, 0], Y[:, 1], marker='.', c='r', alpha=0.2)
        fig.savefig(os.path.join(self.outdir, outname))
        plt.close()


simple_gan_batch_size = 64
simple_gan_nb_z = 20
simple_gan_nb_out = 2
simple_gan_z_shape = (simple_gan_batch_size, simple_gan_nb_z)
simple_gan_real_shape = (simple_gan_batch_size, simple_gan_nb_out)


@pytest.fixture()
def simple_gan():
    z = Input(batch_shape=simple_gan_z_shape, name='z')
    generator = sequential([
        Dense(4*simple_gan_nb_z, activation='relu', name='g1'),
        Dense(4*simple_gan_nb_z, activation='relu', name='g2'),
        Dense(simple_gan_nb_out,  name='g_loss'),
    ])(z)

    d_input = Input(batch_shape=simple_gan_real_shape, name='data')

    discriminator = sequential([
        Dense(400, input_dim=2, name='d1'),
        LeakyReLU(0.3),
        Dense(400, name='d2'),
        LeakyReLU(0.3),
        Dense(1, activation='sigmoid', name='d_loss')
    ])(d_input)
    g = Model(z, generator)
    g.compile(Adam(lr=0.0002, beta_1=0.5), {'g_loss': 'binary_crossentropy'})
    d = Model(d_input,  discriminator)
    d.compile(Adam(lr=0.0002, beta_1=0.5), {'d_loss': 'binary_crossentropy'})
    return GAN(g, d)


def test_metrics_names(simple_gan):
    assert simple_gan.metrics_names == ['g_loss', 'd_loss']


def test_gan_learn_simple_distribution(simple_gan):
    gan = simple_gan

    def sample_multivariate(nb_samples):
        mean = (0.2, 0)
        cov = [[0.1, 0.03],
               [0.02, 0.04]]
        return np.random.multivariate_normal(mean, cov, (nb_samples,))

    # dataset = sample_multivariate
    dataset = sample_circle

    def generator(bs=32):
        while True:
            X = dataset(bs)
            z = np.random.uniform(-1, 1, (bs, simple_gan_nb_z))
            yield {'real': X, 'z': z}

    X = dataset(5000)
    callbacks = [Plotter(X, TEST_OUTPUT_DIR + "/epoches_plot")]

    bs = 64
    gan.fit_generator(generator(bs), nb_batches_per_epoch=100, nb_epoch=1, verbose=1,
                      callbacks=callbacks, batch_size=bs)


def test_gan_utility_funcs(simple_gan: GAN):
    xy_shp = simple_gan_z_shape[1:]
    x = np.zeros(xy_shp, dtype=np.float32)
    y = np.zeros(xy_shp, dtype=np.float32)
    simple_gan.interpolate(x, y)

    z_point = simple_gan.random_z_point()
    neighbors = simple_gan.neighborhood(z_point, std=0.05)

    diff = np.stack([neighbors[0]]*len(neighbors)) - neighbors
    assert np.abs(diff).mean() < 0.1


def test_gan_save_weights(tmpdir):
    z_shape = (1, 8, 8)
    gen_cond = Input(shape=(1, 8, 8), name='gen_cond')

    def get_generator():
        z = Input(shape=z_shape, name='z')

        inputs = [z, gen_cond]
        gen_input = merge(inputs, mode='concat', concat_axis=1)
        gen_output = Convolution2D(10, 2, 2, activation='relu',
                                   border_mode='same')(gen_input)
        g = Model(inputs, gen_output)
        g.compile('adam', 'binary_crossentropy')
        return g

    def get_discriminator():
        dis_input = Input(z_shape, name='data')
        dis_conv = Convolution2D(5, 2, 2, activation='relu')(dis_input)
        dis_flatten = Flatten()(dis_conv)
        dis = Dense(1, activation='sigmoid')(dis_flatten)
        d =  Model(dis_input, dis)
        d.compile('adam', 'binary_crossentropy')
        return d

    gan = GAN(get_generator(), get_discriminator())
    gan.save_weights(str(tmpdir + "/{}.hdf5"))

    gan_load = GAN(get_generator(), get_discriminator())

    all_equal = True
    for s, l in zip(gan.g.layers + gan.d.layers, gan_load.g.layers + gan_load.d.layers):
        if not all([
            (sw.get_value() == lw.get_value()).all()
            for sw, lw in zip(s.trainable_weights, l.trainable_weights)
        ]):
            all_equal = False
    assert not all_equal

    gan_load.g.load_weights(str(tmpdir.join("generator.hdf5")))

    for s, l in zip(gan.g.layers, gan_load.g.layers):
        for sw, lw in zip(s.trainable_weights, l.trainable_weights):
            assert (sw.get_value() == lw.get_value()).all()

    gan_load.d.load_weights(str(tmpdir.join("discriminator.hdf5")))

    for s, l in zip(gan.d.layers, gan_load.d.layers):
        for sw, lw in zip(s.trainable_weights, l.trainable_weights):
            assert (sw.get_value() == lw.get_value()).all()
