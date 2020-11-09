import boto3
from botocore.exceptions import ClientError
import time

FE_TAGS = ['fe-0', 'fe-1']
BE_TAGS = ['be', 'db']

ec2_client_east_1 = boto3.client('ec2', region_name='us-east-1')
ec2_resource_east_1 = boto3.resource('ec2', region_name='us-east-1')
ec2_terminate_waiter_east_1 = ec2_client_east_1.get_waiter('instance_terminated')
ec2_running_waiter_east_1 = ec2_client_east_1.get_waiter('instance_running')

ec2_client_east_2 = boto3.client('ec2', region_name='us-east-2')
ec2_resource_east_2 = boto3.resource('ec2', region_name='us-east-2')
waiter_east_2 = ec2_client_east_2.get_waiter('instance_terminated')
ec2_running_waiter_east_2 = ec2_client_east_2.get_waiter('instance_running')

elbv2_client_east_1 = boto3.client('elbv2', region_name='us-east-1')
lb_waiter_east_1 = elbv2_client_east_1.get_waiter('load_balancers_deleted')

#deletando o load balancer
lbs = elbv2_client_east_1.describe_load_balancers()
for lb in lbs['LoadBalancers']:
    if lb['LoadBalancerName'] == 'fe-load-balancer':

        #deletando o listener
        listeners = elbv2_client_east_1.describe_listeners(LoadBalancerArn=lb['LoadBalancerArn'])
        for listener in listeners['Listeners']:
            elbv2_client_east_1.delete_listener(ListenerArn=listener['ListenerArn'])

        elbv2_client_east_1.delete_load_balancer(LoadBalancerArn=lb['LoadBalancerArn'])
        lb_waiter_east_1.wait(LoadBalancerArns=[lb['LoadBalancerArn']])
        break

#deletando o target group
tgs = elbv2_client_east_1.describe_target_groups()
for tg in tgs['TargetGroups']:
    if tg['TargetGroupName'] == 'fe-lb-target-group':
        elbv2_client_east_1.delete_target_group(TargetGroupArn=tg['TargetGroupArn'])
        break

#terminando as instancias 

fe_filter = [
    {
        'Name': 'tag:Pedro', 
        'Values': FE_TAGS
    }
]

be_filter = [
    {
        'Name': 'tag:Pedro', 
        'Values': BE_TAGS
    }
]

print('Encerrando instancias FE')

instances_east_1 = ec2_resource_east_1.instances.filter(Filters=fe_filter).terminate()

if len(instances_east_1) > 0:
    old_instances_east_1 = [instance['InstanceId'] for instance in instances_east_1[0]['TerminatingInstances']]
    ec2_terminate_waiter_east_1.wait(InstanceIds=old_instances_east_1)

print('Instancias FE encerradas\n')

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
    for group in response['SecurityGroups']:
        ec2_client_east_1.delete_security_group(GroupId=group["GroupId"])

response = ec2_client_east_2.describe_security_groups(
    Filters=[
        dict(Name='group-name', Values=['be-security-group', 'db-security-group'])
    ]
)

if len(response['SecurityGroups']) > 0:
    for group in response['SecurityGroups']:
        ec2_client_east_2.delete_security_group(GroupId=group["GroupId"])

#criando o security group do FE
security_group = ec2_resource_east_1.create_security_group(GroupName="fe-security-group",Description='fe-security-group')
security_group.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=22,ToPort=22)
security_group.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=8080,ToPort=8080)
security_group.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=80,ToPort=80)

#subindo as maquinas
print("Criando instancias FE")
fe_instances_ids = []
for tag in FE_TAGS:
    create_instance_response = ec2_resource_east_1.create_instances(
        ImageId='ami-022a15fa814727a16', 
        MinCount=1, 
        MaxCount=1,
        InstanceType='t2.micro',
        TagSpecifications=[
            {
                'ResourceType': 'instance',
                'Tags': [{"Key": "Pedro", "Value": tag}, {"Key": "Name", "Value": f"Pedro-{tag}"}]
            }
        ],
        SecurityGroupIds=[security_group.id],
        KeyName="PedroCosta",
    )
    fe_instances_ids.append(create_instance_response[0].id)

ec2_running_waiter_east_1.wait(InstanceIds=fe_instances_ids)
print("Instancias FE criadas\n")

#salvando os subnets ids
subnets = ec2_resource_east_1.subnets.all()
subnets_ids = [subnet.id for subnet in subnets]

#pegando o vpc_id default
vpcs = ec2_client_east_1.describe_vpcs()
for vpc in vpcs['Vpcs']:
    if vpc['IsDefault']:
        vpc_id = vpc['VpcId']
    break

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
#registrando targets
elbv2_client_east_1.register_targets(
    TargetGroupArn=create_tg_response['TargetGroups'][0]['TargetGroupArn'],
    Targets=[ {'Id': i_id} for i_id in fe_instances_ids]
)

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

#criando o security group do BE
fe_instances = ec2_client_east_1.describe_instances(InstanceIds=fe_instances_ids)
fe_intences_ips = [instance['Instances'][0]['PublicIpAddress'] for instance in fe_instances['Reservations']]

security_group_be = ec2_resource_east_2.create_security_group(GroupName="be-security-group",Description='be-security-group')
security_group_be.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=22,ToPort=22)
for ip in fe_intences_ips:
    security_group_be.authorize_ingress(IpProtocol="tcp",CidrIp=f"{ip}/32",FromPort=80,ToPort=80)

print("Criando instancias BE")
be_instances_ids = []

create_instance_response = ec2_resource_east_2.create_instances(
    ImageId='ami-0e82959d4ed12de3f', 
    MinCount=1, 
    MaxCount=1,
    InstanceType='t2.micro',
    TagSpecifications=[
        {
            'ResourceType': 'instance',
            'Tags': [{"Key": "Pedro", "Value": BE_TAGS[0]}, {"Key": "Name", "Value": f"Pedro-{BE_TAGS[0]}"}]
        }
    ],
    SecurityGroupIds=[security_group_be.id],
    KeyName="PedroCosta",
)
be_instances_ids.append(create_instance_response[0].id)
time.sleep(10)
ec2_running_waiter_east_2.wait(InstanceIds=be_instances_ids)
print("Instancias BE criadas\n")

#criando o security group do DB
be_instances = ec2_client_east_2.describe_instances(InstanceIds=be_instances_ids)
be_intences_ips = [instance['Instances'][0]['PublicIpAddress'] for instance in be_instances['Reservations']]

security_group_db = ec2_resource_east_2.create_security_group(GroupName="db-security-group",Description='db-security-group')
security_group_db.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=22,ToPort=22)
for ip in be_intences_ips:
    security_group_db.authorize_ingress(IpProtocol="tcp",CidrIp=f"{ip}/32",FromPort=80,ToPort=80)

print("Criando instancias DB")

create_instance_response = ec2_resource_east_2.create_instances(
    ImageId='ami-0e82959d4ed12de3f', 
    MinCount=1, 
    MaxCount=1,
    InstanceType='t2.micro',
    TagSpecifications=[
        {
            'ResourceType': 'instance',
            'Tags': [{"Key": "Pedro", "Value": BE_TAGS[1]}, {"Key": "Name", "Value": f"Pedro-{BE_TAGS[1]}"}]
        }
    ],
    SecurityGroupIds=[security_group_db.id],
    KeyName="PedroCosta",
)
be_instances_ids.append(create_instance_response[0].id)

ec2_running_waiter_east_2.wait(InstanceIds=be_instances_ids)
print("Instancias DB criadas\n")