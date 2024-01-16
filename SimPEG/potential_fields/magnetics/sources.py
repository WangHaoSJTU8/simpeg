from ...survey import BaseSrc
from SimPEG.utils.mat_utils import dip_azimuth2cartesian
from SimPEG.utils.code_utils import validate_float


class UniformBackgroundField(BaseSrc):
    """A constant uniform background magnetic field.

    This inducing field has a uniform magnetic background field defined by an amplitude,
    inclination, and declination.

    Parameters
    ----------
    receiver_list : list of SimPEG.potential_fields.magnetics.Point
    parameters : tuple of (amplitude, inclutation, declination), optional
        Deprecated input for the function, provided in this position for backwards
        compatibility
    amplitude : float, optional
        amplitude of the inducing backgound field, usually this is in units of nT.
    inclination : float, optional
        Dip angle in degrees from the horizon, positive points into the earth.
    declination : float, optional
        Azimuthal angle in degrees from north, positive clockwise.
    """

    def __init__(
        self,
        receiver_list=None,
        amplitude=50000.0,
        inclination=90.0,
        declination=0.0,
        **kwargs,
    ):
        # Raise errors on 'parameters' argument
        #   The parameters argument was supported in the deprecated SourceField
        #   class. We would like to raise an error in case the user passes it
        #   so the class doesn't behave differently than expected.
        if (key := "parameters") in kwargs:
            raise TypeError(
                f"'{key}' property has been removed."
                "Please pass the amplitude, inclination and declination"
                " through their own arguments."
            )

        self.amplitude = amplitude
        self.inclination = inclination
        self.declination = declination

        super().__init__(receiver_list=receiver_list, **kwargs)

    @property
    def amplitude(self):
        """Amplitude of the inducing background field.

        Returns
        -------
        float
            Usually this is in nT. It should match the units of your magnetic data.
        """
        return self._amplitude

    @amplitude.setter
    def amplitude(self, value):
        self._amplitude = validate_float("amplitude", value)

    @property
    def inclination(self):
        """Dip angle in from the horizon.

        Returns
        -------
        float
            In degrees, positive points into the earth
        """
        return self._inclination

    @inclination.setter
    def inclination(self, value):
        self._inclination = validate_float(
            "inclination", value, min_val=-90.0, max_val=90.0
        )

    @property
    def declination(self):
        """Azimuthal angle from north.

        Returns
        -------
        float
            In degrees, positive is clockwise
        """
        return self._declination

    @declination.setter
    def declination(self, value):
        self._declination = validate_float("declination", value)

    @property
    def b0(self):
        return (
            self.amplitude
            * dip_azimuth2cartesian(self.inclination, self.declination).squeeze()
        )
