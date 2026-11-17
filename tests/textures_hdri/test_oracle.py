"""Sanity checks of independently derived references before candidate data exists."""
import math,unittest
import oracle

class OracleTests(unittest.TestCase):
    def test_texture_orientation_and_centers(self):
        chart=[[(1.,0.,0.),(0.,1.,0.)],[(0.,0.,1.),(1.,1.,1.)]]
        for filtertype in ('closest','linear'):
            for x,y in ((0,0),(1,0),(0,1),(1,1)):
                self.assertEqual(oracle.sample_texture(chart,(x+.5)/2,1-(y+.5)/2,filtertype),chart[y][x])
        self.assertEqual(oracle.sample_texture(chart,.5,.5),(.5,.5,.5))

    def test_addressing(self):
        chart=[[(1.,0.,0.),(0.,1.,0.)],[(0.,0.,1.),(1.,1.,1.)]]
        self.assertEqual(oracle.sample_texture(chart,1.25,.75,'closest'),(1.,0.,0.))
        self.assertEqual(oracle.sample_texture(chart,1.25,.75,'closest','mirror'),(0.,1.,0.))
        self.assertEqual(oracle.sample_texture(chart,-.25,.75,'closest','mirror'),(1.,0.,0.))
        self.assertEqual(oracle.sample_texture(chart,-2,.75,'linear','clamp'),(1.,0.,0.))
        self.assertEqual(oracle.sample_texture(chart,-2,.75,'linear','constant',default=(.1,.2,.3)),(.1,.2,.3))

    def test_srgb(self):
        for value,want in ((0,0),(.04045,.0031308049535603713),(.5,.21404114048223255),(1,1)):
            self.assertAlmostEqual(oracle.srgb_to_linear(value),want,places=14)

    def test_directions(self):
        for uv,want in (((.5,.5),(0,0,1)),((.25,.5),(1,0,0)),((.75,.5),(-1,0,0)),((.5,0),(0,1,0))):
            for value,expected in zip(oracle.latlong_direction(*uv),want):self.assertAlmostEqual(value,expected,places=14)
        self.assertEqual(oracle.pole_axis((0,1,0),'Z'),(0,0,1))
        self.assertEqual(oracle.pole_axis((0,0,1),'Z'),(0,-1,0))

    def test_solid_angle(self):
        for w,h in ((2,1),(8,4),(31,17)):
            self.assertAlmostEqual(sum(w*oracle.cell_solid_angle(w,h,y) for y in range(h)),4*math.pi,places=13)

    def test_cosine_and_constant_environment(self):
        self.assertAlmostEqual(oracle.positive_cosine_z_integral(0,1,-1,1),.5,places=14)
        self.assertAlmostEqual(oracle.positive_cosine_z_integral(1,0,-1,1),math.pi/2,places=14)
        rows=[[(.2,.6,3.)]*4 for _ in range(2)]
        for normal in ((0,1,0),(0,0,1),(.6,.8,0)):
            for result in (oracle.lambert_piecewise_latlong(rows,normal),oracle.lambert_bilinear_latlong(rows,normal,16)):
                for value,want in zip(result,(.2,.6,3.)):self.assertAlmostEqual(value,want,delta=2e-5)

    def test_importance_normalization_and_support(self):
        black=[[(0.,0.,0.)]*8 for _ in range(4)];sun=[list(r) for r in black];sun[0][7]=(1e6,1e6,1e6)
        for rows in (black,sun):
            p=oracle.importance_distribution(rows)
            self.assertAlmostEqual(sum(map(sum,p)),1,places=14);self.assertGreater(min(min(row) for row in p),0)
        self.assertAlmostEqual(oracle.environment_pdf(black,(0,1,0)),1/(4*math.pi),places=14)

if __name__=='__main__':unittest.main()
