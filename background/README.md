#  Integration of a Closed-Loop Autonomous Workflow on an AM-DMF Device
## Kailey McGady’s Summer SULI Project at Argonne National Laboratory

### Overview:
Traditional experimentation can be time-consuming, resource-intensive, and subject to human error, making large experimental search spaces difficult to explore efficiently. This project investigated the use of autonomous experimentation and digital microfluidics to reduce experimentation time, improve consistency, and lower the material and energy costs of scientific research. An automated digital microfluidic (AM-DMF) device from ACX Instruments was used to move dyed water droplets and recreate the color-mixing experiment developed in Argonne National Laboratory's Rapid Prototyping Laboratory (RPL). A Python program used a camera to measure the resulting color after each experiment and applied Bayesian optimization to determine the next color mixture. The autonomous workflow successfully demonstrated closed-loop decision making and established a foundation for applying digital microfluidics, robotics, and machine learning to more complex microscale experiments.

## Example Diagram of a Closed-Loop Autonomous Work Flow
![Closed-Loop Workflow Diagram](images/closed-loop-workflow-diagram.png)

## Diagram of the system and its interaction with the code:
![AM-DMF Python Script Steps](images/am-dmf-steps.png)
 

## Breakdown of the software at work: 
ACX DLL Interface --> Python Controller --> AM-DMF Chip --> Camera --> OpenCV --> Average RGB --> Bayesian Optimization --> Next Experiment

## Project Objectives:
- Develop a closed-loop autonomous experimentation workflow.
- Interface Python with an ACX AM-DMF device.
- Detect droplet color using OpenCV.
- Apply Bayesian Optimization to select future experiments.
- Demonstrate autonomous decision-making with minimal human intervention.

## Future work:
•	Integration into a larger autonomous system via MADSci
•	Experimentation using the current OT-2 experiment designs running at the lab
•	Additional machine learning and AI integrated into the software
•	Integration of the chip into a larger system to function as a diagnostic tool 

## Acknowledgements:
This research was conducted through the U.S. Department of Energy Science Undergraduate Laboratory Internships (SULI) program at Argonne National Laboratory.

I would like to express my sincere gratitude to Casey Stone for her mentorship, technical guidance, and encouragement throughout this project. Her support greatly expanded my understanding of autonomous experimentation, digital microfluidics, computer vision, and scientific software development.
I’d also like to thank ACX Instruments for providing the Automated Digital Microfluidic (AM-DMF) platform used in this work. The AM-DMF device was designed and created by them. For more info on the company and their device you can find them here; https://www.acxinst.com/

Finally, I am grateful to the U.S. Department of Energy Office of Science for supporting my undergraduate research. This project reflects the collaborative efforts of many researchers who generously shared their knowledge and expertise, and I am thankful to have contributed to the development of autonomous laboratory technologies. This experience has strengthened my passion for research, and I look forward to continuing to work in this field.  

