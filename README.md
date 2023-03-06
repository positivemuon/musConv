# musConv


#1-->supcgen.py
Generates a nearly cubic supercell (SC) for convergence checks.
Inserts a muon in the supercell at a Voronoi interstitial site.
One of it methods initializes the supercell generation and the other 
re-initializes generation of a larger supercell-size than the former.

To quickly run the code try:
```python musConv/supcgen.py example/LiF.cif```


#2-->chkconv.py
Checks if a supercell (SC) size is converged for muon site calculations
using results of atomic forces from a one shot SCF calculation.

To quickly run the code try:
```python musConv/chkconv.py example/LiF_p1.cif example/LiF_p1_forces.txt```

#Structure of the classes
The structure of the classes are very tentative and will be adjusted depending on the structure of the Aiida WorkChain.

#TO DO
i)Restructure class after the workchain is up
ii) Configure setup.py including dependencies
iii) Do documentation
