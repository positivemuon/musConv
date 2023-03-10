#ALOT OF WORK STILL HAVE TO BE DONE 
#COMMENTS
#CLEAN-UP
#RE-LOGICING DUPLICATED CODES
#MAKE PARSER, PLUGIN
#RUN MORE EXAMPLES
#RESTRUCTURE
#BETTER VARIABLE AND CLASSES NAME
#BUT THEN THE WORKCHAIN STRUCTURE/LOGIC WILL BE MUCH SIMILAR
#----------------------------
from aiida import orm
from aiida.engine import ToContext, WorkChain, calcfunction,  workfunction
from aiida.plugins import  DataFactory,CalculationFactory
from aiida import load_profile
from aiida.engine import run, submit
from aiida.common.extendeddicts import AttributeDict
import numpy as np
from aiida.engine import if_, while_, return_
from supcgen import SCgenerators
from chkconv import check_SC_convergence
load_profile()


@calcfunction
def init_supcgen(aiida_struc):
    py_struc = aiida_struc.get_pymatgen_structure()
    #
    scg=SCgenerators(py_struc)
    py_SCstruc_mu, SC_matrix, mu_frac_coord =scg.initialize()
    #
    aiida_SCstruc = orm.StructureData(pymatgen = py_SCstruc_mu)
    scmat_node = orm.ArrayData()
    scmat_node.set_array('SC_matrix', SC_matrix)
    vor_node = orm.ArrayData()
    vor_node.set_array('Voronoi_site', np.array(mu_frac_coord))

    return {"SC_struc": aiida_SCstruc, "SCmat": scmat_node, "Vor_site": vor_node}
    

@calcfunction
def re_init_supcgen(aiida_struc,aiida_SCstruc, vor_site, iter_num):
    py_struc   = aiida_struc.get_pymatgen_structure()
    py_SCstruc = aiida_SCstruc.get_pymatgen_structure()
    #
    mu_frac_coord = vor_site.get_array('Voronoi_site')
    #
    scg = SCgenerators(py_struc)
    py_SCstruc_mu, SC_matrix = scg.re_initialize(py_SCstruc,mu_frac_coord,iter_num.value)
    #
    aiida_SCstructure = orm.StructureData(pymatgen = py_SCstruc_mu)
    #
    scmat_node = orm.ArrayData()
    scmat_node.set_array('SC_matrix', SC_matrix)
    
    return {"SC_struc": aiida_SCstructure, "SCmat": scmat_node}
    

#@workfunction
@calcfunction
def check_if_conv_achieved(aiida_structure,traj_out):
    atm_forc   = traj_out.get_array('forces')[0]
    atm_forces = np.array(atm_forc)
    ase_struc  = aiida_structure.get_ase()

    #
    csc   = check_SC_convergence(ase_struc,atm_forces)
    cond  = csc.apply_first_crit()
    cond2 = csc.apply_2nd_crit()
    #
    
    if cond == True and all(cond2):
        return orm.Bool(True)
    else:
        return orm.Bool(False)



def get_pseudos(aiid_struc):
    family  = orm.load_group('SSSP/1.2/PBE/efficiency')   #user pseudo fam
    pseudos = family.get_pseudos(structure=aiid_struc)
    return pseudos


def get_kpoints(aiid_struc, k_density = None):
    if k_density == None:   #remove the if and None later, default already defined in the main input
        k_density = 0.401   #default less than normal for quick testing, 

    kpoints = orm.KpointsData()
    kpoints.set_cell_from_structure(aiid_struc)
    kpoints.set_kpoints_mesh_from_density(k_density, force_parity = False)
    
    return kpoints


PwCalculation = CalculationFactory('quantumespresso.pw')


class muSConvWorkChain(WorkChain):
    @classmethod
    def define(cls, spec):
        """Specify inputs and outputs."""
        super().define(spec)
        
        spec.input("structure", valid_type = orm.StructureData,required = True, help = 'Input initial structure')
        #spec.input('num_units', valid_type = orm.Int, default = lambda: orm.Int(2**3), required=False, help='Number of input unitcell units for the initial supercell') 
        spec.input('kpoints_distance', valid_type = orm.Float, default = lambda: orm.Float(0.401), required = False,
            help = 'The minimum desired distance in 1/?? between k-points in reciprocal space.') #copied from pwbaseworkchain

        spec.expose_inputs(PwCalculation, namespace = 'pwscf',exclude = ('structure','pseudos','kpoints'))   #use the  pw calcjob
        
        spec.outline(cls.init_supcell_gen,
                     cls.run_pw_scf,
                     cls.inspect_run_get_forces,
                     while_(cls.continue_iter)(
                         cls.increment_n_by_one,
                         if_(cls.iteration_num_not_exceeded)(
                             cls.get_larger_cell,
                             cls.run_pw_scf,
                             cls.inspect_run_get_forces
                         )
                         .else_(
                             cls.exit_max_iteration_exceeded,
                         )
                     ),
                     cls.set_outputs,
                    )
        
        
        spec.output('Converged_supercell', valid_type = orm.StructureData, required = True)
        spec.output('Converged_SCmatrix', valid_type = orm.ArrayData, required=True)
        
        spec.exit_code(402, 'ERROR_SUB_PROCESS_FAILED_SCF',message='one of the PwCalculation subprocesses failed')
        spec.exit_code(702, 'ERROR_NUM_CONVERGENCE_ITER_EXCEEDED',message='Max number of supercell convergence reached ')
    

    def init_supcell_gen(self):
        self.ctx.n = 0
        self.ctx.max_it_n = 2 #decide in meeting
        
        result_ini = init_supcgen(self.inputs.structure)
        
        self.ctx.sup_struc_mu = result_ini["SC_struc"]
        self.ctx.musite       = result_ini["Vor_site"]
        self.ctx.sc_matrix    = result_ini["SCmat"]
        
    
    def run_pw_scf(self):
        #
        inputs = AttributeDict(self.exposed_inputs(PwCalculation, namespace='pwscf'))
        
        inputs.structure = self.ctx.sup_struc_mu
        inputs.pseudos   = get_pseudos(self.ctx.sup_struc_mu)
        inputs.kpoints   = get_kpoints(self.ctx.sup_struc_mu,self.inputs.kpoints_distance.value)
        
        
        running = self.submit(PwCalculation, **inputs)
        self.report(f'running SCF calculation {running.pk}')
        
        return ToContext(calculation_run=running)
    


    def inspect_run_get_forces(self):
        calculation = self.ctx.calculation_run
        
        if not calculation.is_finished_ok:
            self.report('PwCalculation<{}> failed with exit status {}' .format(calculation.pk, calculation.exit_status))
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_SCF
        else:

            self.ctx.traj_out = calculation.outputs.output_trajectory
            
    def continue_iter(self):
        #check convergence and decide if to continue the loop
        conv_res = check_if_conv_achieved(self.ctx.sup_struc_mu,self.ctx.traj_out)
        return conv_res.value == False    
        ##This implies
        #if conv_res.value == False:
        #    return True
        #else:
        #    return False   
    
    def increment_n_by_one(self):
        self.ctx.n += 1
        
    def iteration_num_not_exceeded(self): 
        return self.ctx.n <= self.ctx.max_it_n
    
    
    def get_larger_cell(self):
        result_reini = re_init_supcgen(
            self.inputs.structure,
            self.ctx.sup_struc_mu,
            self.ctx.musite,
            self.ctx.n
        )
        
        self.ctx.sup_struc_mu = result_reini["SC_struc"]
        self.ctx.sc_matrix    = result_reini["SCmat"]
        
    
    def exit_max_iteration_exceeded(self):
        self.report('Exiting muSConvWorkChain, Coverged supercell NOT achieved, next iter num <{}> is greater than max iteration number {}' .format(self.ctx.n, self.ctx.max_it_n))
        return self.exit_codes.ERROR_NUM_CONVERGENCE_ITER_EXCEEDED
        
    
    def set_outputs(self):
        self.report('Setting Outputs')
        self.out('Converged_supercell',self.ctx.sup_struc_mu)
        self.out('Converged_SCmatrix', self.ctx.sc_matrix)
        



from pymatgen.io.cif import CifParser
if __name__ == '__main__':
    parser = CifParser("Si.cif")
    py_struc = parser.get_structures()[0]
    aiida_structure = orm.StructureData(pymatgen = py_struc)


    builder=muSConvWorkChain.get_builder()
    structure = aiida_structure
    builder.structure = structure
    codename = 'pw7_0@localhost_serial'
    code = orm.Code.get_from_string(codename)
    builder.pwscf.code = code

    Dict = DataFactory('dict')
    parameters = {
    'CONTROL': {
    'calculation': 'scf',
    'restart_mode': 'from_scratch',
    'tstress':True,
    'tprnfor':True,
    },
    'SYSTEM': {
    'ecutwfc': 30.,
    'ecutrho': 240.,
    'tot_charge': 1.0,
    #'nspin': 2,
    'occupations':'smearing',
    'smearing':'cold',
    'degauss':0.01,

    },
    'ELECTRONS': {
    'conv_thr': 1.e-6,
    'electron_maxstep':300,
    'mixing_beta':0.3,
    }
    }

    builder.pwscf.parameters = Dict(dict=parameters)
    #
    builder.pwscf.metadata.description = 'a PWscf  test SCF'
    builder.pwscf.metadata.options.resources = {'num_machines': 1, 'num_mpiprocs_per_machine' : 1}
    #
    results, node = run.get_node(builder)
    #node.exit_status #to check if the calculation was successful
    #
    #print(results) # from #results, node = run.get_node(builder)
    #py_conv_struct=results['Converged_supercell'].get_pymatgen_structure()
    #py_conv_struct.to(filename="supercell_withmu.cif".format())
    #Sc_matrix=results['Converged_SCmatrix'].get_array('SC_matrix')