import boto3
from botocore.exceptions import ClientError
import time

FE_TAG = 'fe'
BE_TAG = 'be'
DB_TAG = 'db'

fe_filter = [
    {
        'Name': 'tag:Pedro', 
        'Values': [FE_TAG]
    }
]

be_filter = [
    {
        'Name': 'tag:Pedro', 
        'Values': [BE_TAG, DB_TAG]
    }
]

ec2_client_east_1 = boto3.client('ec2', region_name='us-east-1')
ec2_resource_east_1 = boto3.resource('ec2', region_name='us-east-1')
ec2_terminate_waiter_east_1 = ec2_client_east_1.get_waiter('instance_terminated')
ec2_running_waiter_east_1 = ec2_client_east_1.get_waiter('instance_running')

ec2_client_east_2 = boto3.client('ec2', region_name='us-east-2')
ec2_resource_east_2 = boto3.resource('ec2', region_name='us-east-2')
waiter_east_2 = ec2_client_east_2.get_waiter('instance_terminated')
ec2_running_waiter_east_2 = ec2_client_east_2.get_waiter('instance_running')

elbv2_client_east_1 = boto3.client('elbv2', region_name='us-east-1')
lb_deleted_waiter_east_1 = elbv2_client_east_1.get_waiter('load_balancers_deleted')
lb_available_waiter_east_1 = elbv2_client_east_1.get_waiter('load_balancer_available')

asg_client_east_1 = boto3.client('autoscaling', region_name='us-east-1')

#deletando o auto scale group e lauch config
print("Deletando ASG e Lauch Config")
try:
    asg_client_east_1.delete_auto_scaling_group(
        AutoScalingGroupName='pedro-fe-asg',
        ForceDelete=True
        )
except:
    pass

try:
    asg_client_east_1.delete_launch_configuration(LaunchConfigurationName='pedro-as-lc')
except:
    pass

while asg_client_east_1.describe_auto_scaling_groups(AutoScalingGroupNames=['pedro-fe-asg'])['AutoScalingGroups'] != []:
    time.sleep(5)

while asg_client_east_1.describe_launch_configurations(LaunchConfigurationNames=['pedro-as-lc'])['LaunchConfigurations'] != []:
    time.sleep(5)

instances_east_1 = [instance for instance in ec2_resource_east_1.instances.filter(Filters=fe_filter)]

if len(instances_east_1) > 0:
    old_instances_east_1 = [instance.id for instance in instances_east_1]
    ec2_terminate_waiter_east_1.wait(InstanceIds=old_instances_east_1)

print("ASG e Lauch Config deletados\n")

#deletando o load balancer
lbs = elbv2_client_east_1.describe_load_balancers()
for lb in lbs['LoadBalancers']:
    if lb['LoadBalancerName'] == 'fe-load-balancer':

        #deletando o listener
        listeners = elbv2_client_east_1.describe_listeners(LoadBalancerArn=lb['LoadBalancerArn'])
        for listener in listeners['Listeners']:
            elbv2_client_east_1.delete_listener(ListenerArn=listener['ListenerArn'])

        elbv2_client_east_1.delete_load_balancer(LoadBalancerArn=lb['LoadBalancerArn'])
        lb_deleted_waiter_east_1.wait(LoadBalancerArns=[lb['LoadBalancerArn']])
        break

#deletando o target group
tgs = elbv2_client_east_1.describe_target_groups()
for tg in tgs['TargetGroups']:
    if tg['TargetGroupName'] == 'fe-lb-target-group':
        elbv2_client_east_1.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
        break

#terminando as instancias 
print('Encerrando instancias BE')

instances_east_2 = ec2_resource_east_2.instances.filter(Filters=be_filter).terminate()

if len(instances_east_2) > 0:
    old_instances_east_2 = [instance['InstanceId'] for instance in instances_east_2[0]['TerminatingInstances']]
    waiter_east_2.wait(InstanceIds=old_instances_east_2)

print('Instancias BE encerradas\n')

#deletando os security groups
response = ec2_client_east_1.describe_security_groups(
    Filters=[
        dict(Name='group-name', Values=['fe-security-group'])
    ]
)

if len(response['SecurityGroups']) > 0:
    while len(response['SecurityGroups']) > 0:
        for group in response['SecurityGroups']:
            try:
                ec2_client_east_1.delete_security_group(GroupId=group["GroupId"])
            except:
                time.sleep(5)
        response = ec2_client_east_1.describe_security_groups(
            Filters=[dict(Name='group-name', Values=['fe-security-group'])]
            )

response = ec2_client_east_2.describe_security_groups(
    Filters=[
        dict(Name='group-name', Values=['be-security-group', 'db-security-group'])
    ]
)

if len(response['SecurityGroups']) > 0:
    while len(response['SecurityGroups']) > 0:
        for group in response['SecurityGroups']:
            try:
                ec2_client_east_2.delete_security_group(GroupId=group["GroupId"])
            except:
                time.sleep(5)
        response = ec2_client_east_2.describe_security_groups(
            Filters=[dict(Name='group-name', Values=['be-security-group', 'db-security-group'])]
            )
            
#criando o security group do FE
security_group = ec2_resource_east_1.create_security_group(GroupName="fe-security-group",Description='fe-security-group')
security_group.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=22,ToPort=22)
security_group.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=8080,ToPort=8080)

#pegando o vpc_id default
vpcs = ec2_client_east_1.describe_vpcs()
for vpc in vpcs['Vpcs']:
    if vpc['IsDefault']:
        vpc_id = vpc['VpcId']
    break

#salvando os subnets ids
subnets = ec2_resource_east_1.subnets.all()
subnets_ids = []
for subnet in subnets:
    if subnet.vpc_id == vpc_id:
        subnets_ids.append(subnet.id)


#criando o load balancer
create_lb_response = elbv2_client_east_1.create_load_balancer(
                                                            Name='fe-load-balancer',
                                                            Subnets=subnets_ids,
                                                            SecurityGroups=[security_group.id],
                                                            Scheme='internet-facing'
                                                            )

#criando o target group
create_tg_response = elbv2_client_east_1.create_target_group(
                                                            Name='fe-lb-target-group',
                                                            Protocol='HTTP',
                                                            Port=8080,
                                                            VpcId=vpc_id
                                                            )

lb_available_waiter_east_1.wait(LoadBalancerArns=[create_lb_response['LoadBalancers'][0]['LoadBalancerArn']])

#criando o listener
response = elbv2_client_east_1.create_listener(
    DefaultActions=[
        {
            'TargetGroupArn': create_tg_response['TargetGroups'][0]['TargetGroupArn'],
            'Type': 'forward',
        },
    ],
    LoadBalancerArn=create_lb_response['LoadBalancers'][0]['LoadBalancerArn'],
    Port=8080,
    Protocol='HTTP',
)

print("Criando ASG e Lauch Config")

as_launch_config = asg_client_east_1.create_launch_configuration(
    LaunchConfigurationName="pedro-as-lc",
    ImageId="ami-03c2ca830ba5df568",
    SecurityGroups=[security_group.id],
    InstanceType="t2.micro",
    InstanceMonitoring={'Enabled': False },
    EbsOptimized=False,
    AssociatePublicIpAddress=True
)

s_ids_str = ""
for sid in subnets_ids:
    if s_ids_str != "":
        s_ids_str += ", "
    s_ids_str += str(sid)

as_group = asg_client_east_1.create_auto_scaling_group(
    AutoScalingGroupName="pedro-fe-asg",
    LaunchConfigurationName="pedro-as-lc",
    MinSize=1,
    MaxSize=4,
    DesiredCapacity=2,
    DefaultCooldown=60,
    HealthCheckType='EC2',
    HealthCheckGracePeriod=60,
    Tags=[{"Key": "Pedro", "Value": FE_TAG}, {"Key": "Name", "Value": f"Pedro-{FE_TAG}"}],
    VPCZoneIdentifier=s_ids_str,
    TargetGroupARNs=[create_tg_response['TargetGroups'][0]['TargetGroupArn']]
)

print("ASG e Lauch Config criados\n")

#criando o security group do BE
# fe_instances = ec2_client_east_1.describe_instances(InstanceIds=fe_instances_ids)
# fe_intences_ips = [instance['Instances'][0]['PublicIpAddress'] for instance in fe_instances['Reservations']]

security_group_be = ec2_resource_east_2.create_security_group(GroupName="be-security-group",Description='be-security-group')
security_group_be.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=22,ToPort=22)
# for ip in fe_intences_ips:
#     security_group_be.authorize_ingress(IpProtocol="tcp",CidrIp=f"{ip}/32",FromPort=5000,ToPort=5000)

security_group_be.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=5000,ToPort=5000)

print("Criando instancias BE")
be_instances_ids = []

create_instance_response = ec2_resource_east_2.create_instances(
    ImageId='ami-0d0eacfe81678f5aa', 
    MinCount=1, 
    MaxCount=1,
    InstanceType='t2.micro',
    TagSpecifications=[
        {
            'ResourceType': 'instance',
            'Tags': [{"Key": "Pedro", "Value": BE_TAG}, {"Key": "Name", "Value": f"Pedro-{BE_TAG}"}]
        }
    ],
    SecurityGroupIds=[security_group_be.id],
    KeyName="PedroCosta",
)
be_instances_ids.append(create_instance_response[0].id)
ec2_running_waiter_east_2.wait(InstanceIds=be_instances_ids)
print("Instancias BE criadas\n")

#associando ao elastic ip
for instance_id in be_instances_ids:
    ec2_client_east_2.associate_address(
        InstanceId = instance_id,
        AllocationId = "eipalloc-04887a6e74e579674"
    )

#criando o security group do DB
be_instances = ec2_client_east_2.describe_instances(InstanceIds=be_instances_ids)
be_intences_ips = [instance['Instances'][0]['PublicIpAddress'] for instance in be_instances['Reservations']]

security_group_db = ec2_resource_east_2.create_security_group(GroupName="db-security-group",Description='db-security-group')
security_group_db.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=22,ToPort=22)
for ip in be_intences_ips:
    security_group_db.authorize_ingress(IpProtocol="tcp",CidrIp=f"{ip}/32",FromPort=80,ToPort=80)

# security_group_db.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=80,ToPort=80)

print("Criando instancias DB")

create_instance_response = ec2_resource_east_2.create_instances(
    ImageId='ami-0684831b77e5bdc83', 
    MinCount=1, 
    MaxCount=1,
    InstanceType='t2.micro',
    TagSpecifications=[
        {
            'ResourceType': 'instance',
            'Tags': [{"Key": "Pedro", "Value": DB_TAG}, {"Key": "Name", "Value": f"Pedro-{DB_TAG}"}]
        }
    ],
    SecurityGroupIds=[security_group_db.id],
    KeyName="PedroCosta",
)
be_instances_ids.append(create_instance_response[0].id)

ec2_running_waiter_east_2.wait(InstanceIds=be_instances_ids)
print("Instancias DB criadas\n")

#associando ao elastic ip
ec2_client_east_2.associate_address(
    InstanceId = be_instances_ids[1],
    AllocationId = "eipalloc-0ec76861dc5c220ad"
)