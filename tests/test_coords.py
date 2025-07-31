from app.fires import FindFires

class TestCoordinates:
    field = (51.398720, -116.491640)
    manning = (49.064646, -120.7919022)

    def test_field(self):
        ff = FindFires(self.field)
        fires = ff.nearby()
        assert(len(fires) == 4)

    def test_manning(self):
        ff = FindFires(self.manning)
        fires = ff.nearby()
        assert(len(fires) == 2)

    def test_out_of_range(self):
        # Middle of the pacific.
        ff = FindFires((40.250308, -152.961979))
        fires = ff.nearby()
        assert(ff.out_of_range())
