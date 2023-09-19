import unittest
from unittest.mock import patch
import pytest
import discretize
from SimPEG import maps
from SimPEG.potential_fields import gravity
from geoana.gravity import Prism
import numpy as np
import os


class TestsGravitySimulation:
    """
    Test gravity simulation.
    """

    @pytest.fixture
    def blocks(self):
        """Synthetic blocks to build the sample model."""
        block1 = np.array([[-1.6, 1.6], [-1.6, 1.6], [-1.6, 1.6]])
        block2 = np.array([[-0.8, 0.8], [-0.8, 0.8], [-0.8, 0.8]])
        rho1 = 1.0
        rho2 = 2.0
        return (block1, block2), (rho1, rho2)

    @pytest.fixture(params=("tensormesh", "treemesh"))
    def mesh(self, blocks, request):
        """Sample mesh."""
        cs = 0.2
        (block1, _), _ = blocks
        if request.param == "tensormesh":
            hxind, hyind, hzind = tuple([(cs, 42)] for _ in range(3))
            mesh = discretize.TensorMesh([hxind, hyind, hzind], "CCC")
        else:
            h = cs * np.ones(64)
            mesh = discretize.TreeMesh([h, h, h], origin="CCC")
            x0, x1 = block1[:, 0], block1[:, 1]
            mesh.refine_box(x0, x1, levels=9)
        return mesh

    @pytest.fixture
    def density_and_active_cells(self, mesh, blocks):
        """Sample density and active_cells arrays for the sample mesh."""
        # create a model of two blocks, 1 inside the other
        (block1, block2), (rho1, rho2) = blocks
        block1_inds = self.get_block_inds(mesh.cell_centers, block1)
        block2_inds = self.get_block_inds(mesh.cell_centers, block2)
        # Define densities for each block
        model = np.zeros(mesh.n_cells)
        model[block1_inds] = rho1
        model[block2_inds] = rho2
        # Define active cells and reduce model
        active_cells = model != 0.0
        model_reduced = model[active_cells]
        return model_reduced, active_cells

    def get_block_inds(self, grid, block):
        return np.where(
            (grid[:, 0] > block[0, 0])
            & (grid[:, 0] < block[0, 1])
            & (grid[:, 1] > block[1, 0])
            & (grid[:, 1] < block[1, 1])
            & (grid[:, 2] > block[2, 0])
            & (grid[:, 2] < block[2, 1])
        )

    @pytest.fixture
    def receivers_locations(self):
        nx = 5
        ny = 5
        # Create plane of observations
        xr = np.linspace(-20, 20, nx)
        yr = np.linspace(-20, 20, ny)
        x, y = np.meshgrid(xr, yr)
        z = np.ones_like(x) * 3.0
        receivers_locations = np.vstack([a.ravel() for a in (x, y, z)]).T
        return receivers_locations

    def get_analytic_solution(self, blocks, survey):
        """Compute analytical response from dense prism."""
        (block1, block2), (rho1, rho2) = blocks
        # Build prisms (convert densities from g/cc to kg/m3)
        prisms = [
            Prism(block1[:, 0], block1[:, 1], rho1 * 1000),
            Prism(block2[:, 0], block2[:, 1], -rho1 * 1000),
            Prism(block2[:, 0], block2[:, 1], rho2 * 1000),
        ]
        # Forward model the prisms
        components = survey.source_field.receiver_list[0].components
        receivers_locations = survey.source_field.receiver_list[0].locations
        if "gx" in components or "gy" in components or "gz" in components:
            fields = sum(
                prism.gravitational_field(receivers_locations) for prism in prisms
            )
            fields *= 1e5  # convert to mGal from m/s^2
        else:
            fields = sum(
                prism.gravitational_gradient(receivers_locations) for prism in prisms
            )
            fields *= 1e9  # convert to Eotvos from 1/s^2
        return fields

    @pytest.mark.parametrize(
        "engine, parallelism",
        [("geoana", None), ("geoana", 1), ("choclo", False), ("choclo", True)],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_accelerations_vs_analytic(
        self,
        engine,
        parallelism,
        store_sensitivities,
        tmp_path,
        blocks,
        mesh,
        density_and_active_cells,
        receivers_locations,
    ):
        """
        Test gravity acceleration components against analytic solutions of prisms.
        """
        components = ["gx", "gy", "gz"]
        # Unpack fixtures
        density, active_cells = density_and_active_cells
        # Create survey
        receivers = gravity.Point(receivers_locations, components=components)
        sources = gravity.SourceField([receivers])
        survey = gravity.Survey(sources)
        # Create reduced identity map for Linear Problem
        idenMap = maps.IdentityMap(nP=int(sum(active_cells)))
        # Create simulation
        if engine == "choclo":
            sensitivity_path = tmp_path / "sensitivity_choclo"
            kwargs = dict(choclo_parallel=parallelism)
        else:
            sensitivity_path = tmp_path
            kwargs = dict(n_processes=parallelism)
        sim = gravity.Simulation3DIntegral(
            mesh,
            survey=survey,
            rhoMap=idenMap,
            ind_active=active_cells,
            store_sensitivities=store_sensitivities,
            engine=engine,
            sensitivity_path=str(sensitivity_path),
            sensitivity_dtype=np.float64,
            **kwargs,
        )
        data = sim.dpred(density)
        g_x, g_y, g_z = data[0::3], data[1::3], data[2::3]
        solution = self.get_analytic_solution(blocks, survey)
        # Check results
        rtol, atol = 1e-9, 1e-6
        np.testing.assert_allclose(g_x, solution[:, 0], rtol=rtol, atol=atol)
        np.testing.assert_allclose(g_y, solution[:, 1], rtol=rtol, atol=atol)
        np.testing.assert_allclose(g_z, solution[:, 2], rtol=rtol, atol=atol)

    @pytest.mark.parametrize(
        "engine, parallelism",
        [("geoana", None), ("geoana", 1), ("choclo", False), ("choclo", True)],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_tensor_vs_analytic(
        self,
        engine,
        parallelism,
        store_sensitivities,
        tmp_path,
        blocks,
        mesh,
        density_and_active_cells,
        receivers_locations,
    ):
        """
        Test tensor components against analytic solutions of prisms.
        """
        components = ["gxx", "gxy", "gxz", "gyy", "gyz", "gzz"]
        # Unpack fixtures
        density, active_cells = density_and_active_cells
        # Create survey
        receivers = gravity.Point(receivers_locations, components=components)
        sources = gravity.SourceField([receivers])
        survey = gravity.Survey(sources)
        # Create reduced identity map for Linear Problem
        idenMap = maps.IdentityMap(nP=int(sum(active_cells)))
        # Create simulation
        if engine == "choclo":
            sensitivity_path = tmp_path / "sensitivity_choclo"
            kwargs = dict(choclo_parallel=parallelism)
        else:
            sensitivity_path = tmp_path
            kwargs = dict(n_processes=parallelism)
        sim = gravity.Simulation3DIntegral(
            mesh,
            survey=survey,
            rhoMap=idenMap,
            ind_active=active_cells,
            store_sensitivities=store_sensitivities,
            engine=engine,
            sensitivity_path=str(sensitivity_path),
            sensitivity_dtype=np.float64,
            **kwargs,
        )
        data = sim.dpred(density)
        g_xx, g_xy, g_xz = data[0::6], data[1::6], data[2::6]
        g_yy, g_yz, g_zz = data[3::6], data[4::6], data[5::6]
        solution = self.get_analytic_solution(blocks, survey)
        # Check results
        rtol, atol = 2e-6, 1e-6
        np.testing.assert_allclose(g_xx, solution[..., 0, 0], rtol=rtol, atol=atol)
        np.testing.assert_allclose(g_xy, solution[..., 0, 1], rtol=rtol, atol=atol)
        np.testing.assert_allclose(g_xz, solution[..., 0, 2], rtol=rtol, atol=atol)
        np.testing.assert_allclose(g_yy, solution[..., 1, 1], rtol=rtol, atol=atol)
        np.testing.assert_allclose(g_yz, solution[..., 1, 2], rtol=rtol, atol=atol)
        np.testing.assert_allclose(g_zz, solution[..., 2, 2], rtol=rtol, atol=atol)

    @pytest.mark.parametrize(
        "engine, parallelism",
        [("geoana", 1), ("geoana", None), ("choclo", False), ("choclo", True)],
        ids=["geoana_serial", "geoana_parallel", "choclo_serial", "choclo_parallel"],
    )
    @pytest.mark.parametrize("store_sensitivities", ("ram", "disk", "forward_only"))
    def test_guv_vs_analytic(
        self,
        engine,
        parallelism,
        store_sensitivities,
        tmp_path,
        blocks,
        mesh,
        density_and_active_cells,
        receivers_locations,
    ):
        """
        Test guv tensor component against analytic solutions of prisms.
        """
        components = ["guv"]
        # Unpack fixtures
        density, active_cells = density_and_active_cells
        # Create survey
        receivers = gravity.Point(receivers_locations, components=components)
        sources = gravity.SourceField([receivers])
        survey = gravity.Survey(sources)
        # Create reduced identity map for Linear Problem
        idenMap = maps.IdentityMap(nP=int(sum(active_cells)))
        # Create simulation
        if engine == "choclo":
            sensitivity_path = tmp_path / "sensitivity_choclo"
            kwargs = dict(choclo_parallel=parallelism)
        else:
            sensitivity_path = tmp_path
            kwargs = dict(n_processes=parallelism)
        sim = gravity.Simulation3DIntegral(
            mesh,
            survey=survey,
            rhoMap=idenMap,
            ind_active=active_cells,
            store_sensitivities=store_sensitivities,
            engine=engine,
            sensitivity_path=str(sensitivity_path),
            sensitivity_dtype=np.float64,
            **kwargs,
        )
        g_uv = sim.dpred(density)
        solution = self.get_analytic_solution(blocks, survey)
        g_xx_solution = solution[..., 0, 0]
        g_yy_solution = solution[..., 1, 1]
        g_uv_solution = 0.5 * (g_yy_solution - g_xx_solution)
        # Check results
        rtol, atol = 2e-6, 1e-6
        np.testing.assert_allclose(g_uv, g_uv_solution, rtol=rtol, atol=atol)

    def test_invalid_engine(self, mesh, density_and_active_cells, receivers_locations):
        """Test if error is raised after invalid engine."""
        # Unpack fixtures
        _, active_cells = density_and_active_cells
        # Create survey
        receivers = gravity.Point(receivers_locations, components="gz")
        sources = gravity.SourceField([receivers])
        survey = gravity.Survey(sources)
        # Create reduced identity map for Linear Problem
        idenMap = maps.IdentityMap(nP=int(sum(active_cells)))
        # Check if error is raised after an invalid engine is passed
        engine = "invalid engine"
        with pytest.raises(ValueError, match=f"Invalid engine '{engine}'"):
            gravity.Simulation3DIntegral(
                mesh,
                survey=survey,
                rhoMap=idenMap,
                ind_active=active_cells,
                engine=engine,
            )

    def test_choclo_and_n_proceesses(
        self, mesh, density_and_active_cells, receivers_locations
    ):
        """Check if warning is raised after passing n_processes with choclo engine."""
        # Unpack fixtures
        _, active_cells = density_and_active_cells
        # Create survey
        receivers = gravity.Point(receivers_locations, components="gz")
        sources = gravity.SourceField([receivers])
        survey = gravity.Survey(sources)
        # Create reduced identity map for Linear Problem
        idenMap = maps.IdentityMap(nP=int(sum(active_cells)))
        # Check if warning is raised
        msg = "The 'n_processes' will be ignored when selecting 'choclo'"
        with pytest.warns(UserWarning, match=msg):
            gravity.Simulation3DIntegral(
                mesh,
                survey=survey,
                rhoMap=idenMap,
                ind_active=active_cells,
                engine="choclo",
                n_processes=2,
            )

    def test_choclo_and_sensitivity_path_as_dir(
        self, mesh, density_and_active_cells, receivers_locations, tmp_path
    ):
        """
        Check if error is raised when sensitivity_path is a dir with choclo engine.
        """
        # Unpack fixtures
        _, active_cells = density_and_active_cells
        # Create survey
        receivers = gravity.Point(receivers_locations, components="gz")
        sources = gravity.SourceField([receivers])
        survey = gravity.Survey(sources)
        # Create reduced identity map for Linear Problem
        idenMap = maps.IdentityMap(nP=int(sum(active_cells)))
        # Create a sensitivity_path directory
        sensitivity_path = tmp_path / "sensitivity_dummy"
        sensitivity_path.mkdir()
        # Check if error is raised
        msg = f"The passed sensitivity_path '{str(sensitivity_path)}' is a directory"
        with pytest.raises(ValueError, match=msg):
            gravity.Simulation3DIntegral(
                mesh,
                survey=survey,
                rhoMap=idenMap,
                ind_active=active_cells,
                store_sensitivities="disk",
                sensitivity_path=str(sensitivity_path),
                engine="choclo",
            )

    @patch("SimPEG.potential_fields.gravity.simulation.choclo", None)
    def test_choclo_missing(self, mesh, density_and_active_cells, receivers_locations):
        """
        Check if error is raised when choclo is missing and chosen as engine.
        """
        # Unpack fixtures
        _, active_cells = density_and_active_cells
        # Create survey
        receivers = gravity.Point(receivers_locations, components="gz")
        sources = gravity.SourceField([receivers])
        survey = gravity.Survey(sources)
        # Create reduced identity map for Linear Problem
        idenMap = maps.IdentityMap(nP=int(sum(active_cells)))
        # Check if error is raised
        msg = "The choclo package couldn't be found."
        with pytest.raises(ImportError, match=msg):
            gravity.Simulation3DIntegral(
                mesh,
                survey=survey,
                rhoMap=idenMap,
                ind_active=active_cells,
                engine="choclo",
            )


class TestConversionFactor:
    """Test _get_conversion_factor function."""

    @pytest.mark.parametrize(
        "component",
        ("gx", "gy", "gz", "gxx", "gyy", "gzz", "gxy", "gxz", "gyz", "guv"),
    )
    def test_conversion_factor(self, component):
        """
        Test _get_conversion_factor function with valid components
        """
        conversion_factor = gravity.simulation._get_conversion_factor(component)
        if len(component) == 2:
            assert conversion_factor == 1e5 * 1e3  # SI to mGal and g/cc to kg/m3
        else:
            assert conversion_factor == 1e9 * 1e3  # SI to Eotvos and g/cc to kg/m3

    def test_invalid_conversion_factor(self):
        """
        Test invalid conversion factor _get_conversion_factor function
        """
        component = "invalid-component"
        with pytest.raises(ValueError, match=f"Invalid component '{component}'"):
            gravity.simulation._get_conversion_factor(component)


def test_ana_grav_forward(tmp_path):
    nx = 5
    ny = 5

    # Define a mesh
    cs = 0.2
    hxind = [(cs, 41)]
    hyind = [(cs, 41)]
    hzind = [(cs, 41)]
    mesh = discretize.TensorMesh([hxind, hyind, hzind], "CCC")

    # create a model of two blocks, 1 inside the other
    block1 = np.array([[-1.5, 1.5], [-1.5, 1.5], [-1.5, 1.5]])
    block2 = np.array([[-0.7, 0.7], [-0.7, 0.7], [-0.7, 0.7]])

    def get_block_inds(grid, block):
        return np.where(
            (grid[:, 0] > block[0, 0])
            & (grid[:, 0] < block[0, 1])
            & (grid[:, 1] > block[1, 0])
            & (grid[:, 1] < block[1, 1])
            & (grid[:, 2] > block[2, 0])
            & (grid[:, 2] < block[2, 1])
        )

    block1_inds = get_block_inds(mesh.cell_centers, block1)
    block2_inds = get_block_inds(mesh.cell_centers, block2)

    rho1 = 1.0
    rho2 = 2.0

    model = np.zeros(mesh.n_cells)
    model[block1_inds] = rho1
    model[block2_inds] = rho2

    active_cells = model != 0.0
    model_reduced = model[active_cells]

    # Create reduced identity map for Linear Pproblem
    idenMap = maps.IdentityMap(nP=int(sum(active_cells)))

    # Create plane of observations
    xr = np.linspace(-20, 20, nx)
    yr = np.linspace(-20, 20, ny)
    X, Y = np.meshgrid(xr, yr)
    Z = np.ones_like(X) * 3.0
    locXyz = np.c_[X.reshape(-1), Y.reshape(-1), Z.reshape(-1)]

    receivers = gravity.Point(locXyz, components=["gx", "gy", "gz"])
    sources = gravity.SourceField([receivers])
    survey = gravity.Survey(sources)

    sim = gravity.Simulation3DIntegral(
        mesh,
        survey=survey,
        rhoMap=idenMap,
        ind_active=active_cells,
        store_sensitivities="disk",
        sensitivity_path=str(tmp_path) + os.sep,
    )

    with pytest.raises(TypeError):
        sim.sensitivity_dtype = float

    assert sim.sensitivity_dtype is np.float32

    data = sim.dpred(model_reduced)
    d_x = data[0::3]
    d_y = data[1::3]
    d_z = data[2::3]

    # Compute analytical response from dense prism
    prism_1 = Prism(block1[:, 0], block1[:, 1], rho1 * 1000)  # g/cc to kg/m**3
    prism_2 = Prism(block2[:, 0], block2[:, 1], -rho1 * 1000)
    prism_3 = Prism(block2[:, 0], block2[:, 1], rho2 * 1000)

    d = (
        prism_1.gravitational_field(locXyz)
        + prism_2.gravitational_field(locXyz)
        + prism_3.gravitational_field(locXyz)
    ) * 1e5  # convert to mGal from m/s^2
    d = d.astype(sim.sensitivity_dtype)
    np.testing.assert_allclose(d_x, d[:, 0], rtol=1e-9, atol=1e-6)
    np.testing.assert_allclose(d_y, d[:, 1], rtol=1e-9, atol=1e-6)
    np.testing.assert_allclose(d_z, d[:, 2], rtol=1e-9, atol=1e-6)


def test_ana_gg_forward():
    nx = 5
    ny = 5

    # Define a mesh
    cs = 0.2
    hxind = [(cs, 41)]
    hyind = [(cs, 41)]
    hzind = [(cs, 41)]
    mesh = discretize.TensorMesh([hxind, hyind, hzind], "CCC")

    # create a model of two blocks, 1 inside the other
    block1 = np.array([[-1.5, 1.5], [-1.5, 1.5], [-1.5, 1.5]])
    block2 = np.array([[-0.7, 0.7], [-0.7, 0.7], [-0.7, 0.7]])

    def get_block_inds(grid, block):
        return np.where(
            (grid[:, 0] > block[0, 0])
            & (grid[:, 0] < block[0, 1])
            & (grid[:, 1] > block[1, 0])
            & (grid[:, 1] < block[1, 1])
            & (grid[:, 2] > block[2, 0])
            & (grid[:, 2] < block[2, 1])
        )

    block1_inds = get_block_inds(mesh.cell_centers, block1)
    block2_inds = get_block_inds(mesh.cell_centers, block2)

    rho1 = 1.0
    rho2 = 2.0

    model = np.zeros(mesh.n_cells)
    model[block1_inds] = rho1
    model[block2_inds] = rho2

    active_cells = model != 0.0
    model_reduced = model[active_cells]

    # Create reduced identity map for Linear Pproblem
    idenMap = maps.IdentityMap(nP=int(sum(active_cells)))

    # Create plane of observations
    xr = np.linspace(-20, 20, nx)
    yr = np.linspace(-20, 20, ny)
    X, Y = np.meshgrid(xr, yr)
    Z = np.ones_like(X) * 3.0
    locXyz = np.c_[X.reshape(-1), Y.reshape(-1), Z.reshape(-1)]

    receivers = gravity.Point(
        locXyz, components=["gxx", "gxy", "gxz", "gyy", "gyz", "gzz"]
    )
    sources = gravity.SourceField([receivers])
    survey = gravity.Survey(sources)

    sim = gravity.Simulation3DIntegral(
        mesh,
        survey=survey,
        rhoMap=idenMap,
        ind_active=active_cells,
        store_sensitivities="forward_only",
        n_processes=None,
    )

    # forward only should default to np.float64
    assert sim.sensitivity_dtype is np.float64

    data = sim.dpred(model_reduced)
    d_xx = data[0::6]
    d_xy = data[1::6]
    d_xz = data[2::6]
    d_yy = data[3::6]
    d_yz = data[4::6]
    d_zz = data[5::6]

    # Compute analytical response from dense prism
    prism_1 = Prism(block1[:, 0], block1[:, 1], rho1 * 1000)  # g/cc to kg/m**3
    prism_2 = Prism(block2[:, 0], block2[:, 1], -rho1 * 1000)
    prism_3 = Prism(block2[:, 0], block2[:, 1], rho2 * 1000)

    d = (
        prism_1.gravitational_gradient(locXyz)
        + prism_2.gravitational_gradient(locXyz)
        + prism_3.gravitational_gradient(locXyz)
    ) * 1e9  # convert to Eotvos from 1/s^2

    np.testing.assert_allclose(d_xx, d[..., 0, 0], rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(d_xy, d[..., 0, 1], rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(d_xz, d[..., 0, 2], rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(d_yy, d[..., 1, 1], rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(d_yz, d[..., 1, 2], rtol=1e-10, atol=1e-12)
    np.testing.assert_allclose(d_zz, d[..., 2, 2], rtol=1e-10, atol=1e-12)


if __name__ == "__main__":
    unittest.main()
